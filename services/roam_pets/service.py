"""宠物养成与完整冒险循环：领养、养成、探索、战斗、捕捉和商店。"""
from __future__ import annotations

import random
from datetime import datetime

from . import capture, maps, storage
from .catalog import ELEMENT_CHART, SPECIES, STARTERS, get
from .models import PetState

MAX_LEVEL = 10
FEED_CAP = 95
PLAY_ENERGY_MIN = 15
EXPLORE_ENERGY_MIN = 10
MAX_TEAM = 6
REST_COST = 8

SHOP_ITEMS: dict[str, dict[str, object]] = {
    "basic": {"name": "漫游球", "price": 30, "kind": "ball"},
    "great": {"name": "高级球", "price": 80, "kind": "ball"},
    "ultra": {"name": "究极球", "price": 180, "kind": "ball"},
    "berry": {"name": "星莓", "price": 18, "kind": "food"},
}
RARITY_REWARD = {"普通": 0, "稀有": 8, "史诗": 16}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_shape(data: dict) -> dict:
    base = storage.blank()
    if not isinstance(data, dict):
        return base
    for key, value in base.items():
        if key not in data or data[key] is None:
            data[key] = value
    if not isinstance(data["pets"], dict):
        data["pets"] = {}
    normalized_pets = {}
    for key, value in data["pets"].items():
        if not isinstance(value, dict) or str(key) not in SPECIES:
            continue
        state = PetState.from_dict(value).to_dict()
        state["species_id"] = str(key)
        normalized_pets[str(key)] = state
    data["pets"] = normalized_pets
    dex = data.get("dex") if isinstance(data.get("dex"), dict) else {}
    data["dex"] = {
        "seen": list(dict.fromkeys(str(v) for v in dex.get("seen", []) if str(v) in SPECIES)),
        "caught": list(dict.fromkeys(str(v) for v in dex.get("caught", []) if str(v) in SPECIES)),
    }
    if not isinstance(data.get("window_pos"), dict):
        data["window_pos"] = {}
    if not isinstance(data.get("wallet"), dict):
        data["wallet"] = base["wallet"]
    data["wallet"]["coins"] = max(0, int(data["wallet"].get("coins", 160)))
    inventory = data.get("inventory") if isinstance(data.get("inventory"), dict) else {}
    data["inventory"] = {
        key: max(0, int(inventory.get(key, amount)))
        for key, amount in base["inventory"].items()
    }
    journey = data.get("journey") if isinstance(data.get("journey"), dict) else {}
    data["journey"] = {
        key: max(0, int(journey.get(key, amount)))
        for key, amount in base["journey"].items()
    }
    data["active"] = str(data.get("active") or "") or None
    if data["active"] not in data["pets"]:
        data["active"] = next(iter(data["pets"]), None)
    battle = data.get("battle")
    if not isinstance(battle, dict):
        data["battle"] = None
    elif battle.get("player_id") not in data["pets"] or battle.get("species_id") not in SPECIES:
        data["battle"] = None
    data["version"] = storage.VERSION
    return data


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _assert_no_battle(data: dict) -> None:
    battle = data.get("battle")
    if isinstance(battle, dict):
        raise ValueError("当前遭遇尚未结束，请先完成战斗或撤离。")


def _new_state(species_id: str, nickname: str, *, level: int = 1) -> dict:
    return PetState(
        species_id=species_id,
        nickname=(nickname or "").strip()[:12] or SPECIES[species_id].name,
        level=max(1, min(MAX_LEVEL, int(level))),
        hatched_at=_now(),
        last_seen=_now(),
    ).to_dict()


def adopt(data: dict, species_id: str, nickname: str = "") -> dict:
    species = get(species_id)
    if not species:
        raise ValueError("没有这个物种，无法领养。")
    data = ensure_shape(data)
    _assert_no_battle(data)
    if species_id in data["pets"]:
        raise ValueError(f"{species.name} 已经是你的伙伴了。")
    if len(data["pets"]) >= MAX_TEAM:
        raise ValueError(f"宠物队伍已满（{MAX_TEAM} 只）。")
    state = _new_state(species_id, nickname)
    data["pets"][species_id] = state
    _mark(data, species_id, caught=True)
    if not data["active"]:
        data["active"] = species_id
    return state


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
    _assert_no_battle(data)
    if pet_id not in data["pets"]:
        raise ValueError("这只宠物不在你的队伍里。")
    data["active"] = pet_id
    data["pets"][pet_id]["last_seen"] = _now()
    return data["pets"][pet_id]


def rename(data: dict, pet_id: str, nickname: str) -> dict:
    data = ensure_shape(data)
    state = data["pets"].get(pet_id)
    if state is None:
        raise ValueError("这只宠物不在你的队伍里。")
    name = (nickname or "").strip()[:12]
    if not name:
        raise ValueError("昵称不能为空。")
    state["nickname"] = name
    state["last_seen"] = _now()
    return state

def grant_exp(data: dict, pet_id: str, state: dict, amount: int) -> list[str]:
    events: list[str] = []
    species = SPECIES.get(pet_id)
    state["exp"] = int(state.get("exp", 0)) + max(0, int(amount))
    while state["level"] < MAX_LEVEL and state["exp"] >= state["level"] * 10:
        state["exp"] -= state["level"] * 10
        state["level"] = int(state["level"]) + 1
        if species:
            events.append(f"{state['nickname']} 升到了 Lv.{state['level']}！")
    if state["level"] >= MAX_LEVEL:
        state["exp"] = 0
    return events


def feed(data: dict, pet_id: str | None = None) -> dict:
    data = ensure_shape(data)
    _assert_no_battle(data)
    pet_id = pet_id or data["active"]
    if pet_id not in data["pets"]:
        raise ValueError("先领养一只宠物吧。")
    state = data["pets"][pet_id]
    if int(state["hunger"]) >= FEED_CAP:
        raise ValueError(f"{state['nickname']} 现在很饱，吃不下更多了。")
    state["hunger"] = _clamp(int(state["hunger"]) + 22)
    state["mood"] = _clamp(int(state.get("mood", 50)) + 6)
    state["health"] = _clamp(int(state.get("health", 100)) + 3)
    state["bond"] = _clamp(int(state.get("bond", 0)) + 1)
    events = grant_exp(data, pet_id, state, 1)
    state["last_seen"] = _now()
    return {"state": state, "events": events}


def play(data: dict, pet_id: str | None = None) -> dict:
    data = ensure_shape(data)
    _assert_no_battle(data)
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
    events = grant_exp(data, pet_id, state, 2)
    state["last_seen"] = _now()
    return {"state": state, "events": events}


def use_berry(data: dict, pet_id: str | None = None) -> dict:
    data = ensure_shape(data)
    _assert_no_battle(data)
    pet_id = pet_id or data["active"]
    if pet_id not in data["pets"]:
        raise ValueError("先领养一只宠物吧。")
    if int(data["inventory"].get("berry", 0)) <= 0:
        raise ValueError("星莓已经用完了。")
    state = data["pets"][pet_id]
    data["inventory"]["berry"] -= 1
    state["hunger"] = _clamp(int(state["hunger"]) + 30)
    state["health"] = _clamp(int(state.get("health", 100)) + 18)
    state["mood"] = _clamp(int(state.get("mood", 50)) + 8)
    state["bond"] = _clamp(int(state.get("bond", 0)) + 1)
    events = grant_exp(data, pet_id, state, 2)
    state["last_seen"] = _now()
    return {"state": state, "events": events}


def tick(data: dict, now: datetime | None = None) -> dict:
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
    if int(state.get("health", 100)) < 30:
        return "虚弱"
    if int(state.get("bond", 0)) >= 30 and hunger >= 55:
        return "开心"
    return "平静"


def speak_line(pet_id: str, mood: str) -> str:
    species = get(pet_id)
    if not species or not species.lines:
        return "……"
    index = {"开心": 0, "平静": 1, "饿了": 2, "累了": 1, "虚弱": 2}.get(mood, 1) % len(species.lines)
    return species.lines[index]


def dex_view(data: dict) -> list[dict]:
    data = ensure_shape(data)
    seen = set(data["dex"]["seen"])
    caught = set(data["dex"]["caught"])
    rows = []
    for species_id, species in SPECIES.items():
        rows.append({
            "id": species_id,
            "name": species.name if species_id in seen else "？？？",
            "element": species.element,
            "rarity": species.rarity,
            "title": species.title if species_id in seen else "尚未遇见",
            "flavor": species.flavor if species_id in caught else "——",
            "habitat": species.habitat,
            "status": "caught" if species_id in caught else ("seen" if species_id in seen else "unknown"),
        })
    return rows


def starters_left(data: dict) -> list[str]:
    data = ensure_shape(data)
    return [sid for sid in STARTERS if sid not in data["pets"]]


def _mark(data: dict, species_id: str, caught: bool = False) -> None:
    dex = data["dex"]
    if species_id not in dex["seen"]:
        dex["seen"].append(species_id)
    if caught and species_id not in dex["caught"]:
        dex["caught"].append(species_id)

def max_team_level(data: dict) -> int:
    data = ensure_shape(data)
    return max((int(state["level"]) for state in data["pets"].values()), default=1)


def shop_catalog(data: dict) -> list[dict]:
    data = ensure_shape(data)
    return [
        {
            "id": item_id,
            "name": item["name"],
            "price": item["price"],
            "kind": item["kind"],
            "count": int(data["inventory"].get(item_id, 0)),
        }
        for item_id, item in SHOP_ITEMS.items()
    ]


def buy_item(data: dict, item_id: str, quantity: int = 1) -> dict:
    data = ensure_shape(data)
    item = SHOP_ITEMS.get(item_id)
    if not item:
        raise ValueError("商店没有这个物品。")
    quantity = max(1, min(10, int(quantity or 1)))
    cost = int(item["price"]) * quantity
    if int(data["wallet"]["coins"]) < cost:
        raise ValueError("同行币不足。")
    data["wallet"]["coins"] -= cost
    data["inventory"][item_id] = int(data["inventory"].get(item_id, 0)) + quantity
    return {"item_id": item_id, "name": item["name"], "quantity": quantity, "cost": cost}


def rest_team(data: dict) -> dict:
    data = ensure_shape(data)
    _assert_no_battle(data)
    if not data["pets"]:
        raise ValueError("先领养一只宠物吧。")
    if int(data["wallet"]["coins"]) < REST_COST:
        raise ValueError("同行币不足，无法休整。")
    data["wallet"]["coins"] -= REST_COST
    for state in data["pets"].values():
        state["energy"] = _clamp(int(state["energy"]) + 28)
        state["health"] = _clamp(int(state.get("health", 100)) + 18)
        state["mood"] = _clamp(int(state.get("mood", 50)) + 4)
        state["last_seen"] = _now()
    return {"cost": REST_COST, "message": "全队休整完毕。"}


def _weighted_choice(rows: list[tuple[str, int]], rng: random.Random) -> str:
    total = sum(weight for _, weight in rows)
    pick = rng.uniform(0, total)
    cursor = 0.0
    for species_id, weight in rows:
        cursor += weight
        if pick <= cursor:
            return species_id
    return rows[-1][0]


def _player_max_hp(state: dict) -> int:
    species = SPECIES[state["species_id"]]
    return species.stat("hp") + int(state["level"]) * 5


def _element_multiplier(attacker: str, defender: str) -> float:
    chart = ELEMENT_CHART.get(attacker, {"strong": [], "weak": []})
    if defender in chart["strong"]:
        return 1.25
    if defender in chart["weak"]:
        return 0.8
    return 1.0


def _damage(attacker_level: int, attack: int, defender_def: int,
            attacker_element: str, defender_element: str, rng: random.Random) -> int:
    base = attack * 0.55 + attacker_level * 2.2 - defender_def * 0.22
    damage = round(max(2.0, base) * _element_multiplier(attacker_element, defender_element) * rng.uniform(0.88, 1.12))
    return max(2, damage)


def _sync_health(state: dict, battle: dict) -> None:
    maximum = max(1, int(battle["player_max_hp"]))
    state["health"] = _clamp(round(int(battle["player_hp"]) / maximum * 100))


def _battle_log(battle: dict, line: str) -> None:
    battle.setdefault("log", []).append(line)
    battle["log"] = battle["log"][-8:]


def explore(data: dict, map_id: str, rng: random.Random | None = None) -> dict:
    data = ensure_shape(data)
    if data.get("battle"):
        raise ValueError("先结束当前遭遇，再前往其他地图。")
    scene = maps.MAPS.get(map_id)
    if not scene:
        raise ValueError("没有这张地图。")
    if max_team_level(data) < scene.unlock_level:
        raise ValueError(f"{scene.name} 需要队伍最高等级达到 Lv.{scene.unlock_level}。")
    active = active_state(data)
    if not active:
        raise ValueError("先领养一只宠物吧。")
    pet_id, state = active
    if int(state["energy"]) < EXPLORE_ENERGY_MIN:
        raise ValueError("当前伙伴体力不足，先休整或换一只伙伴。")
    if int(state.get("health", 100)) <= 0:
        raise ValueError("当前伙伴需要先恢复生命。")
    rng = rng or random.Random()
    enemy_id = _weighted_choice(list(scene.encounters), rng)
    enemy = SPECIES[enemy_id]
    enemy_level = max(1, min(MAX_LEVEL, int(state["level"]) + rng.choice((-1, 0, 0, 0, 1))))
    enemy_max_hp = enemy.stat("hp") + enemy_level * 4
    player_max_hp = _player_max_hp(state)
    player_hp = max(1, round(player_max_hp * int(state.get("health", 100)) / 100))
    state["energy"] = _clamp(int(state["energy"]) - 3)
    state["last_seen"] = _now()
    _mark(data, enemy_id, caught=False)
    data["journey"]["steps"] += 1
    data["journey"]["battles"] += 1
    data["battle"] = {
        "status": "active",
        "map_id": map_id,
        "species_id": enemy_id,
        "enemy_level": enemy_level,
        "enemy_hp": enemy_max_hp,
        "enemy_max_hp": enemy_max_hp,
        "player_id": pet_id,
        "player_hp": player_hp,
        "player_max_hp": player_max_hp,
        "log": [f"在{scene.name}遇见了野生的{enemy.name}！"],
        "last_capture": None,
    }
    return data["battle"]


def _enemy_strike(data: dict, rng: random.Random) -> None:
    battle = data["battle"]
    player_species = SPECIES[battle["player_id"]]
    enemy_species = SPECIES[battle["species_id"]]
    damage = _damage(
        int(battle["enemy_level"]), enemy_species.stat("atk"), player_species.stat("def"),
        enemy_species.element, player_species.element, rng,
    )
    battle["player_hp"] = max(0, int(battle["player_hp"]) - damage)
    _sync_health(data["pets"][battle["player_id"]], battle)
    _battle_log(battle, f"{enemy_species.name}反击，造成 {damage} 点伤害。")
    if int(battle["player_hp"]) <= 0:
        battle["status"] = "lost"
        _battle_log(battle, f"{player_species.name}失去战斗能力，本次遭遇失败。")

def attack(data: dict, rng: random.Random | None = None) -> dict:
    data = ensure_shape(data)
    battle = data.get("battle")
    if not isinstance(battle, dict) or battle.get("status") != "active":
        raise ValueError("当前没有进行中的战斗。")
    rng = rng or random.Random()
    player = SPECIES[battle["player_id"]]
    enemy = SPECIES[battle["species_id"]]
    damage = _damage(
        int(data["pets"][battle["player_id"]]["level"]), player.stat("atk"), enemy.stat("def"),
        player.element, enemy.element, rng,
    )
    battle["enemy_hp"] = max(0, int(battle["enemy_hp"]) - damage)
    _battle_log(battle, f"{player.name}攻击，造成 {damage} 点伤害。")
    if int(battle["enemy_hp"]) <= 0:
        exp = 12 + int(battle["enemy_level"]) * 2
        reward = 16 + int(battle["enemy_level"]) * 3 + RARITY_REWARD.get(enemy.rarity, 0)
        data["wallet"]["coins"] += reward
        data["journey"]["wins"] += 1
        events = grant_exp(data, battle["player_id"], data["pets"][battle["player_id"]], exp)
        battle["status"] = "won"
        battle["reward"] = {"coins": reward, "exp": exp, "events": events}
        _battle_log(battle, f"战胜了{enemy.name}，获得 {reward} 同行币。")
        return battle
    _enemy_strike(data, rng)
    return battle


def capture_wild(data: dict, ball_id: str, rng: random.Random | None = None) -> dict:
    data = ensure_shape(data)
    battle = data.get("battle")
    if not isinstance(battle, dict) or battle.get("status") != "active":
        raise ValueError("当前没有可以捕捉的野生宠物。")
    if ball_id not in capture.BALLS:
        raise ValueError("没有这种精灵球。")
    if int(data["inventory"].get(ball_id, 0)) <= 0:
        raise ValueError("这种精灵球已经用完了。")
    species_id = str(battle["species_id"])
    if species_id in data["pets"]:
        raise ValueError("你已经拥有这只宠物，可以继续战斗获得奖励。")
    if len(data["pets"]) >= MAX_TEAM:
        raise ValueError(f"宠物队伍已满（{MAX_TEAM} 只），无法再捕捉。")
    rng = rng or random.Random()
    data["inventory"][ball_id] -= 1
    player_species = SPECIES[battle["player_id"]]
    result = capture.attempt(
        ball_id, species_id,
        hp_ratio=int(battle["enemy_hp"]) / max(1, int(battle["enemy_max_hp"])),
        rng=rng,
        player_element=player_species.element,
    )
    battle["last_capture"] = result
    shakes = sum(1 for value in result["shakes"] if value)
    _battle_log(battle, f"投出{capture.BALLS[ball_id]['name']}，摇动了 {shakes} 次。")
    if result["caught"]:
        state = _new_state(species_id, SPECIES[species_id].name, level=int(battle["enemy_level"]))
        data["pets"][species_id] = state
        _mark(data, species_id, caught=True)
        data["journey"]["captures"] += 1
        exp = 8 + int(battle["enemy_level"])
        reward = 10 + int(battle["enemy_level"]) * 2
        data["wallet"]["coins"] += reward
        events = grant_exp(data, battle["player_id"], data["pets"][battle["player_id"]], exp)
        battle["status"] = "caught"
        battle["reward"] = {"coins": reward, "exp": exp, "events": events}
        _battle_log(battle, f"捕捉成功！{SPECIES[species_id].name}加入了队伍。")
    else:
        _battle_log(battle, f"{SPECIES[species_id].name}挣脱了！")
        _enemy_strike(data, rng)
    return battle


def close_battle(data: dict) -> str:
    data = ensure_shape(data)
    battle = data.get("battle")
    if not isinstance(battle, dict):
        raise ValueError("当前没有遭遇。")
    status = str(battle.get("status") or "active")
    data["battle"] = None
    return status


def battle_species(data: dict) -> tuple[object | None, object | None]:
    data = ensure_shape(data)
    battle = data.get("battle")
    if not isinstance(battle, dict):
        return None, None
    return SPECIES.get(battle.get("player_id")), SPECIES.get(battle.get("species_id"))
