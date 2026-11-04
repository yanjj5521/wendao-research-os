"""灵兽园：养成、地图探索、战斗、结契、商店与桌宠管理。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services.roam_pets import maps as roam_maps
from services.roam_pets import service as roam_service
from services.roam_pets import storage as roam_storage
from services.roam_pets.catalog import SPECIES, STARTERS, get as get_species
from services import professions
from services.roam_pets.capture import BALLS
from web.runtime import context, flash, templates

app = APIRouter()
_ROOT = Path(__file__).resolve().parents[2]


def _load() -> dict:
    return roam_service.tick(roam_storage.load())


def _update(mutator):
    def apply(data):
        return mutator(roam_service.tick(data))
    return roam_storage.update(apply)


def _redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


@app.get("/pets", response_class=HTMLResponse, name="roam_page")
def roam_page(request: Request):
    data = _load()
    active = roam_service.active_state(data)
    active_species = get_species(active[0]) if active else None
    starters = roam_service.starters_left(data)
    mood = roam_service.mood_of(active[1]) if active else "平静"
    owned = [(sid, state, get_species(sid)) for sid, state in data["pets"].items()]
    max_level = roam_service.max_team_level(data)
    battle = data.get("battle") if isinstance(data.get("battle"), dict) else None
    player_species, enemy_species = roam_service.battle_species(data)
    battle_map = roam_maps.MAPS.get(str(battle.get("map_id"))) if battle else None
    maps_view = [
        {
            "scene": scene,
            "unlocked": max_level >= scene.unlock_level,
            "encounter_names": [
                SPECIES[species_id].name
                for species_id, _weight in scene.encounters
                if species_id in SPECIES
            ],
        }
        for scene in roam_maps.MAPS.values()
    ]
    balls_view = [
        {
            "id": ball_id,
            "name": ball["name"],
            "bonus": ball["bonus"],
            "price": ball["price"],
            "count": int(data["inventory"].get(ball_id, 0)),
        }
        for ball_id, ball in BALLS.items()
    ]
    return templates.TemplateResponse(
        request=request,
        name="roam.html",
        context=context(
            request,
            "roam",
            active_state=active[1] if active else None,
            active_species=active_species,
            active_mood=mood,
            starters=[get_species(sid) for sid in starters],
            dex=roam_service.dex_view(data),
            owned=owned,
            maps=maps_view,
            balls=balls_view,
            wallet=data["wallet"],
            inventory=data["inventory"],
            journey=data["journey"],
            shop=roam_service.shop_catalog(data),
            battle=battle,
            battle_map=battle_map,
            battle_player=player_species,
            battle_enemy=enemy_species,
            max_level=max_level,
            species_count=len(SPECIES),
        ),
    )


@app.post("/pets/adopt", name="roam_adopt")
def roam_adopt(request: Request, species_id: str = Form(""), nickname: str = Form("")):
    if species_id not in STARTERS:
        flash(request, "该伙伴需要通过野外结契获得。", "error")
        return _redirect(request)
    try:
        state, _ = _update(lambda data: roam_service.adopt(data, species_id, nickname))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, f"{state['nickname']} 加入了你的研究旅程！桌宠可随时唤出。", "success")
    return _redirect(request)


@app.post("/pets/active", name="roam_set_active")
def roam_set_active(request: Request, species_id: str = Form("")):
    try:
        state, _ = _update(lambda data: roam_service.set_active(data, species_id))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, f"现在跟随你的是 {state['nickname']}。", "success")
    return _redirect(request)


@app.post("/pets/rename", name="roam_rename")
def roam_rename(request: Request, species_id: str = Form(""), nickname: str = Form("")):
    try:
        state, _ = _update(lambda data: roam_service.rename(data, species_id, nickname))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, f"新昵称已保存：{state['nickname']}。", "success")
    return _redirect(request)


@app.post("/pets/feed", name="roam_feed")
def roam_feed(request: Request, species_id: str = Form("")):
    try:
        result, _ = _update(lambda data: roam_service.feed(data, species_id or None))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        state = result["state"]
        events = "；".join(result["events"])
        flash(request, f"{state['nickname']} 吃得很香（饥饿 {state['hunger']}）。{events}", "success")
    return _redirect(request)


@app.post("/pets/play", name="roam_play")
def roam_play(request: Request, species_id: str = Form("")):
    try:
        result, _ = _update(lambda data: roam_service.play(data, species_id or None))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        state = result["state"]
        events = "；".join(result["events"])
        flash(request, f"{state['nickname']} 玩得很开心（体力 {state['energy']}）。{events}", "success")
    return _redirect(request)


@app.post("/pets/berry", name="roam_use_berry")
def roam_use_berry(request: Request, species_id: str = Form("")):
    try:
        result, _ = _update(lambda data: roam_service.use_berry(data, species_id or None))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        state = result["state"]
        events = "；".join(result["events"])
        flash(request, f"已使用星莓，{state['nickname']} 恢复精神。{events}", "success")
    return _redirect(request)


@app.post("/pets/shop", name="roam_buy")
def roam_buy(request: Request, item_id: str = Form(""), quantity: int = Form(1)):
    try:
        result, _ = _update(lambda data: roam_service.buy_item(data, item_id, quantity))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, f"已购买 {result['name']} ×{result['quantity']}，花费 {result['cost']} 同行币。", "success")
    return _redirect(request)


@app.post("/pets/rest", name="roam_rest")
def roam_rest(request: Request):
    try:
        result, _ = _update(roam_service.rest_team)
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, f"{result['message']} 花费 {result['cost']} 同行币。", "success")
    return _redirect(request)


@app.post("/pets/explore", name="roam_explore")
def roam_explore(request: Request, map_id: str = Form("")):
    try:
        battle, _ = _update(lambda data: roam_service.explore(data, map_id))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        enemy = SPECIES[battle["species_id"]]
        flash(request, f"在{roam_maps.MAPS[map_id].name}遇见了野外的{enemy.name}！", "success")
    return _redirect(request)


@app.post("/pets/battle/attack", name="roam_battle_attack")
def roam_battle_attack(request: Request):
    try:
        battle, _ = _update(roam_service.attack)
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        if battle["status"] == "won":
            flash(request, f"战斗胜利，获得 {battle['reward']['coins']} 同行币。", "success")
        elif battle["status"] == "lost":
            flash(request, "本次战斗失败，返回乐园休整后再试。", "error")
        else:
            flash(request, battle["log"][-1], "success")
    return _redirect(request)

@app.post("/pets/battle/capture", name="roam_battle_capture")
def roam_battle_capture(request: Request, ball_id: str = Form("")):
    bonus = 1.0 + (professions.bonus("beastmaster") - 1.0) * 0.5
    try:
        battle, _ = _update(lambda data: roam_service.capture_wild(data, ball_id, bonus=bonus))
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        if battle["status"] == "caught":
            enemy = SPECIES[battle["species_id"]]
            outcome = professions.add_exp("beastmaster", 15)
            level_up = f"，晋阶 {outcome['tier']}" if outcome.get("leveled") else ""
            flash(
                request,
                f"结契成功！{enemy.name} 已入灵兽袋。御兽师熟练度 +15{level_up}",
                "success",
            )
        elif battle["status"] == "lost":
            flash(request, "灵契球没能留住它，你的伙伴也失去了战斗能力。", "error")
        else:
            attempt = battle.get("last_capture") or {}
            shakes = sum(1 for value in attempt.get("shakes", []) if value)
            flash(request, f"摇动了 {shakes} 次后挣脱了。", "error")
    return _redirect(request)


@app.post("/pets/battle/close", name="roam_battle_close")
def roam_battle_close(request: Request):
    try:
        status, _ = _update(roam_service.close_battle)
    except (ValueError, OSError, TimeoutError) as exc:
        flash(request, str(exc), "error")
    else:
        flash(request, "已返回灵兽园。" if status == "fled" else "遭遇已结束。", "success")
    return _redirect(request)


@app.post("/pets/launch", name="roam_launch")
def roam_launch(request: Request, species_id: str = Form("")):
    data = _load()
    target = species_id or data.get("active") or (next(iter(data["pets"]), "") if data["pets"] else "")
    if not target or target not in data["pets"]:
        flash(request, "先与一只灵兽结契，再唤出桌宠。", "error")
        return _redirect(request)
    try:
        _launch_window(str(target))
    except OSError as exc:
        flash(request, f"唤出桌宠失败：{exc}", "error")
    else:
        flash(request, f"{data['pets'][target]['nickname']} 已出现在桌面上（可拖动，右键菜单可喂食/收回）。", "success")
    return _redirect(request)


def _launch_window(species_id: str) -> None:
    """唤出独立桌宠窗口进程（冻结 EXE 走 CLI 子命令，源码模式走 run_local 分发）。"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "run-roam-pet", "--species", species_id]
    else:
        cmd = [sys.executable, str(_ROOT / "run_local.py"), "run-roam-pet", "--species", species_id]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        cmd,
        cwd=str(_ROOT),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
