"""宠物乐园：桌宠管理、图鉴与（预留的）捕捉/地图入口。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from services.roam_pets import maps as roam_maps
from services.roam_pets import service as roam_service
from services.roam_pets import storage as roam_storage
from services.roam_pets.catalog import STARTERS, get as get_species
from services.roam_pets.capture import BALLS
from web.runtime import context, flash, templates

app = APIRouter()
_ROOT = Path(__file__).resolve().parents[2]


def _load() -> dict:
    return roam_service.tick(roam_storage.load())


@app.get("/pets", response_class=HTMLResponse, name="roam_page")
def roam_page(request: Request):
    data = _load()
    active = roam_service.active_state(data)
    active_species = get_species(active[0]) if active else None
    starters = roam_service.starters_left(data)
    mood = roam_service.mood_of(active[1]) if active else "平静"
    owned = [(sid, state, get_species(sid)) for sid, state in data["pets"].items()]
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
            maps=roam_maps.MAPS.values(),
            balls=BALLS,
            species_count=len(roam_service.SPECIES) if hasattr(roam_service, "SPECIES") else 0,
        ),
    )


@app.post("/pets/adopt", name="roam_adopt")
def roam_adopt(request: Request, species_id: str = Form(""), nickname: str = Form("")):
    data = _load()
    if species_id not in STARTERS:
        flash(request, "该伙伴尚未开放获取，等捕捉系统上线吧。", "error")
        return RedirectResponse(request.url_for("roam_page"), status_code=303)
    try:
        state = roam_service.adopt(data, species_id, nickname)
    except ValueError as exc:
        flash(request, str(exc), "error")
    else:
        roam_storage.save(data)
        flash(request, f"{state['nickname']} 加入了你的研究旅程！桌宠可随时唤出。", "success")
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


@app.post("/pets/active", name="roam_set_active")
def roam_set_active(request: Request, species_id: str = Form("")):
    data = _load()
    try:
        state = roam_service.set_active(data, species_id)
    except ValueError as exc:
        flash(request, str(exc), "error")
    else:
        roam_storage.save(data)
        flash(request, f"现在跟随你的是 {state['nickname']}。", "success")
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


@app.post("/pets/feed", name="roam_feed")
def roam_feed(request: Request, species_id: str = Form("")):
    data = _load()
    try:
        result = roam_service.feed(data, species_id or None)
    except ValueError as exc:
        flash(request, str(exc), "error")
    else:
        roam_storage.save(data)
        state = result["state"]
        events = "；".join(result["events"])
        flash(request, f"{state['nickname']} 吃得很香（饥饿 {state['hunger']}）。{events}", "success")
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


@app.post("/pets/play", name="roam_play")
def roam_play(request: Request, species_id: str = Form("")):
    data = _load()
    try:
        result = roam_service.play(data, species_id or None)
    except ValueError as exc:
        flash(request, str(exc), "error")
    else:
        roam_storage.save(data)
        state = result["state"]
        events = "；".join(result["events"])
        flash(request, f"{state['nickname']} 玩得很开心（体力 {state['energy']}）。{events}", "success")
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


@app.post("/pets/launch", name="roam_launch")
def roam_launch(request: Request, species_id: str = Form("")):
    data = _load()
    target = species_id or data.get("active") or (next(iter(data["pets"]), "") if data["pets"] else "")
    if not target or target not in data["pets"]:
        flash(request, "先领养一只宠物，再唤出桌宠。", "error")
        return RedirectResponse(request.url_for("roam_page"), status_code=303)
    try:
        _launch_window(str(target))
    except OSError as exc:
        flash(request, f"唤出桌宠失败：{exc}", "error")
    else:
        flash(request, f"{data['pets'][target]['nickname']} 已出现在桌面上（可拖动，右键菜单可喂食/收回）。", "success")
    return RedirectResponse(request.url_for("roam_page"), status_code=303)


def _launch_window(species_id: str) -> None:
    """唤出独立桌宠窗口进程（冻结 EXE 走 CLI 子命令，源码模式走 run_local 分发）。"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "run-roam-pet", "--species", species_id]
    else:
        cmd = [sys.executable, str(_ROOT / "run_local.py"), "run-roam-pet", "--species", species_id]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(cmd, cwd=str(_ROOT), creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

