"""洞府与趣味层路由：游戏世界（建筑、法器、资产）、炼丹、休闲空间。

由原 game_world / alchemy / idle_space 三个模块整体合并，
三个路由注册函数名不变（register_game_routes / register_alchemy_routes / register_idle_routes）。
重复的 import 为拼接保留，Python 语义上无害。"""
from __future__ import annotations

from typing import Any, Callable
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from core.database import connect, now_iso, total_xp
from services.economy import ASSETS, balance, balances, transact
from services.game_world import (
    ARTIFACTS,
    BUILDINGS,
    building_cost,
    building_level,
    equipped_artifact,
    inventory_map,
)
from services.online_sync import best_effort_sync, queue_event
from services.profile_media import (
    MAX_AVATAR_BYTES,
    current_avatar_filename,
    remove_avatar,
    save_avatar_bytes,
)
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from core.runtime_paths import STORAGE_ROOT
from services.ai_provider import provider_status
from services.file_storage import MAX_UPLOAD_BYTES, save_upload_limited
from services.review_engine import add_or_increment_herb, generate_special_task
from core.database import get_setting, set_setting
from services.idle_space import (
    BUILTIN_WALLPAPERS,
    custom_wallpapers,
    current_wallpaper_filename,
    save_wallpaper_bytes,
    select_builtin_wallpaper,
    select_custom_wallpaper,
    wallpaper_url,
)

# ==================== 以下为路由与页面逻辑 ====================

# ==================== 游戏世界（洞府） ====================






def register_game_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
    current_realm: Callable[[int], dict[str, Any]],
):
    router = APIRouter()

    @router.get("/world", response_class=HTMLResponse, name="world_page")
    def world_page(request: Request):
        with connect() as conn:
            wallet = balances(conn)
            inventory = inventory_map(conn)
            building_cards = []
            for key, spec in BUILDINGS.items():
                level = building_level(conn, key)
                building_cards.append({"key": key, **spec, "level": level, "cost": building_cost(key, level)})
            artifact_cards = []
            for key, spec in ARTIFACTS.items():
                owned = inventory.get(key)
                level = int(owned["level"]) if owned else 0
                price = int(spec["price"])
                max_level = int(spec.get("max_level", 1))
                artifact_cards.append({
                    "key": key,
                    **spec,
                    "owned": bool(owned),
                    "equipped": bool(owned and owned["equipped"]),
                    "level": level,
                    "max_level": max_level,
                    "upgrade_cost": max(10, (price // 2) * max(level, 1)),
                    "can_afford": int(wallet["spirit_stone"]) >= price,
                    "shortfall": max(0, price - int(wallet["spirit_stone"])),
                })
            herb_specs = {
                1: {"label": "凡品", "name": "青露草", "icon": "♧"},
                2: {"label": "灵品", "name": "凝神花", "icon": "☘"},
                3: {"label": "玄品", "name": "玄脉芝", "icon": "⚘"},
                4: {"label": "地品", "name": "地心莲", "icon": "✿"},
                5: {"label": "天品", "name": "天衍果", "icon": "❋"},
            }
            quantities = {
                int(row["grade"]): int(row["quantity"])
                for row in conn.execute("SELECT grade,quantity FROM herb_inventory")
            }
            herbs = [
                {"grade": grade, **spec, "quantity": quantities.get(grade, 0)}
                for grade, spec in herb_specs.items()
            ]
            if herbs and all(item["quantity"] > 0 for item in herbs):
                conn.execute("UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='all_herbs'", (now_iso(),))
                conn.commit()
            eggs = [dict(row) for row in conn.execute("SELECT * FROM easter_eggs ORDER BY unlocked DESC,title")]
            profile = dict(conn.execute("SELECT * FROM player_profile WHERE id=1").fetchone())
            active_artifact = equipped_artifact(conn)
        return templates.TemplateResponse(
            request=request,
            name="world.html",
            context=context(request, "world", wallet=wallet, assets=ASSETS, buildings=building_cards,
                            artifacts=artifact_cards, herbs=herbs, eggs=eggs, profile=profile,
                            active_artifact=active_artifact,
                            egg_count=sum(1 for egg in eggs if egg["unlocked"])),
        )

    @router.post("/world/buildings/{building_key}/upgrade", name="world_building_upgrade")
    def world_building_upgrade(request: Request, building_key: str):
        if building_key not in BUILDINGS:
            raise HTTPException(status_code=404)
        with connect() as conn:
            level = building_level(conn, building_key)
            if level >= 9:
                flash(request, "该建筑已达到当前版本上限。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            spec = BUILDINGS[building_key]
            cost = building_cost(building_key, level)
            try:
                transact(conn, spec["asset"], -cost, f"升级建筑：{spec['name']}")
            except ValueError:
                flash(request, f"{ASSETS[spec['asset']]['name']}不足，还需要 {cost}。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            ts = now_iso()
            conn.execute(
                """INSERT INTO inventory_items(item_key,item_type,quantity,level,equipped,acquired_at,updated_at)
                   VALUES (?,?,1,1,0,?,?)
                   ON CONFLICT(item_key) DO UPDATE SET level=inventory_items.level+1,updated_at=excluded.updated_at""",
                (building_key, "building", ts, ts),
            )
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("building_upgrade", 6, f"{spec['name']}升至 {level+1} 级", ts))
            queue_event(conn, "building_upgrade", {"item_key": building_key, "level": level + 1})
            conn.commit()
        best_effort_sync()
        flash(request, f"{spec['name']}已升至 {level+1} 级。", "success")
        return RedirectResponse(request.url_for("world_page"), status_code=303)

    @router.post("/world/artifacts/{artifact_key}/buy", name="world_artifact_buy")
    def world_artifact_buy(request: Request, artifact_key: str):
        if artifact_key not in ARTIFACTS:
            raise HTTPException(status_code=404)
        spec = ARTIFACTS[artifact_key]
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM inventory_items WHERE item_key=? AND item_type='artifact'",
                (artifact_key,),
            ).fetchone():
                flash(request, "你已经拥有这件法器。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            try:
                transact(conn, "spirit_stone", -int(spec["price"]), f"购入法器：{spec['name']}")
            except ValueError:
                current = balance(conn, "spirit_stone")
                shortfall = max(0, int(spec["price"]) - current)
                flash(request, f"当前有 {current} 灵石，还差 {shortfall} 灵石才能购入。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            ts = now_iso()
            conn.execute(
                "INSERT INTO inventory_items(item_key,item_type,quantity,level,equipped,acquired_at,updated_at) VALUES (?,?,1,1,0,?,?)",
                (artifact_key, "artifact", ts, ts),
            )
            owned_count = int(
                conn.execute(
                    "SELECT COUNT(*) n FROM inventory_items WHERE item_type='artifact'"
                ).fetchone()["n"]
            )
            if owned_count >= 5:
                conn.execute(
                    """
                    UPDATE easter_eggs
                    SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                    WHERE egg_key='artifact_keeper'
                    """,
                    (ts,),
                )
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("artifact_buy", 3, f"获得法器：{spec['name']}", ts),
            )
            queue_event(conn, "artifact_buy", {"item_key": artifact_key})
            conn.commit()
        best_effort_sync()
        flash(request, f"已获得法器：{spec['name']}。", "success")
        return RedirectResponse(request.url_for("world_page"), status_code=303)

    @router.post("/world/artifacts/{artifact_key}/upgrade", name="world_artifact_upgrade")
    def world_artifact_upgrade(request: Request, artifact_key: str):
        if artifact_key not in ARTIFACTS:
            raise HTTPException(status_code=404)
        spec = ARTIFACTS[artifact_key]
        with connect() as conn:
            row = conn.execute("SELECT * FROM inventory_items WHERE item_key=? AND item_type='artifact'", (artifact_key,)).fetchone()
            if not row:
                flash(request, "请先购入这件法器。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            level = int(row["level"])
            max_level = int(spec.get("max_level", 1))
            if level >= max_level:
                flash(
                    request,
                    "这件法器的作用已经完整，无需继续淬炼。"
                    if max_level == 1
                    else "法器已达到当前版本上限。",
                    "error",
                )
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            cost = max(10, (int(spec["price"]) // 2) * max(level, 1))
            try:
                transact(conn, "spirit_stone", -cost, f"淬炼法器：{spec['name']}")
            except ValueError:
                flash(request, f"灵石不足，淬炼需要 {cost} 灵石。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            conn.execute("UPDATE inventory_items SET level=level+1,updated_at=? WHERE item_key=?", (now_iso(), artifact_key))
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("artifact_upgrade", 5, f"{spec['name']}淬炼至 {level+1} 级", now_iso()))
            queue_event(conn, "artifact_upgrade", {"item_key": artifact_key, "level": level + 1})
            conn.commit()
        best_effort_sync()
        flash(request, f"{spec['name']}已淬炼至 {level+1} 级。", "success")
        return RedirectResponse(request.url_for("world_page"), status_code=303)

    @router.post("/world/artifacts/{artifact_key}/equip", name="world_artifact_equip")
    def world_artifact_equip(request: Request, artifact_key: str):
        with connect() as conn:
            row = conn.execute("SELECT * FROM inventory_items WHERE item_key=? AND item_type='artifact'", (artifact_key,)).fetchone()
            if not row:
                flash(request, "尚未拥有这件法器。", "error")
                return RedirectResponse(request.url_for("world_page"), status_code=303)
            conn.execute("UPDATE inventory_items SET equipped=0 WHERE item_type='artifact'")
            conn.execute("UPDATE inventory_items SET equipped=1,updated_at=? WHERE item_key=?", (now_iso(), artifact_key))
            conn.execute("UPDATE player_profile SET featured_item_key=?,updated_at=? WHERE id=1", (artifact_key, now_iso()))
            queue_event(conn, "artifact_equip", {"item_key": artifact_key})
            conn.commit()
        best_effort_sync()
        effect = ARTIFACTS.get(artifact_key, {}).get("effect", "")
        flash(
            request,
            f"法器已佩戴。当前作用：{effect}" if effect else "法器已佩戴，并展示在个人主页。",
            "success",
        )
        return RedirectResponse(request.url_for("world_page"), status_code=303)

    @router.post("/world/eggs/moon-well", name="world_moon_well")
    def world_moon_well(request: Request):
        with connect() as conn:
            row = conn.execute("SELECT unlocked FROM easter_eggs WHERE egg_key='moon_well'").fetchone()
            if row and not row["unlocked"]:
                conn.execute("UPDATE easter_eggs SET unlocked=1,discovered_at=? WHERE egg_key='moon_well'", (now_iso(),))
                transact(conn, "spirit_stone", 3, "发现隐藏彩蛋：月影井")
                conn.commit()
                flash(request, "你在月影井边发现了 3 枚灵石。", "success")
            else:
                flash(request, "井水映出一句话：真正的问题，往往藏在你没有测量的地方。", "success")
        return RedirectResponse(request.url_for("world_page"), status_code=303)

    @router.get("/profile", response_class=HTMLResponse, name="profile_page")
    def profile_page(request: Request, edit: int = 0):
        with connect() as conn:
            profile = dict(conn.execute("SELECT * FROM player_profile WHERE id=1").fetchone())
            wallet = balances(conn)
            xp = total_xp(conn)
            realm = current_realm(xp)
            owned_artifacts = []
            for row in conn.execute("SELECT * FROM inventory_items WHERE item_type='artifact' ORDER BY acquired_at"):
                item = dict(row)
                item.update(ARTIFACTS.get(row["item_key"], {"name": row["item_key"], "icon": "◇", "desc": ""}))
                owned_artifacts.append(item)
            stats = {
                "deliveries": conn.execute("SELECT COUNT(*) n FROM mission_deliveries").fetchone()["n"],
                "documents": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind='document'").fetchone()["n"],
                "notes": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind IN ('note','question','idea','sop','failure')").fetchone()["n"],
                "datasets": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind='dataset'").fetchone()["n"],
                "experiments": conn.execute("SELECT COUNT(*) n FROM experiments").fetchone()["n"],
                "simulations": conn.execute("SELECT COUNT(*) n FROM simulations").fetchone()["n"],
            }
        profile["skills_list"] = [x.strip() for x in profile["skills"].splitlines() if x.strip()]
        profile["capabilities_list"] = [x.strip() for x in profile["capabilities"].splitlines() if x.strip()]
        profile["avatar_file"] = current_avatar_filename()
        return templates.TemplateResponse(
            request=request, name="profile.html",
            context=context(request, "profile", profile=profile, wallet=wallet, xp=xp, realm=realm,
                            artifacts=owned_artifacts, stats=stats, herbs=[], edit_mode=bool(edit)),
        )

    @router.post("/profile", name="profile_save")
    def profile_save(
        request: Request,
        display_name: str = Form(...), title: str = Form(""), bio: str = Form(""),
        skills: str = Form(""), capabilities: str = Form(""), goals: str = Form(""), avatar_symbol: str = Form("道"),
    ):
        with connect() as conn:
            conn.execute(
                """UPDATE player_profile SET display_name=?,title=?,bio=?,skills=?,capabilities=?,goals=?,avatar_symbol=?,updated_at=? WHERE id=1""",
                (display_name.strip() or "修士", title.strip(), bio.strip(), skills.strip(), capabilities.strip(), goals.strip(), (avatar_symbol.strip() or "道")[:2], now_iso()),
            )
            conn.execute("INSERT INTO settings(key,value) VALUES ('researcher_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (display_name.strip() or "修士",))
            queue_event(
                conn,
                "profile_updated",
                {
                    "display_name": display_name.strip() or "修士",
                    "title": title.strip(), "bio": bio.strip(), "skills": skills.strip(),
                    "capabilities": capabilities.strip(), "goals": goals.strip(),
                    "avatar_symbol": (avatar_symbol.strip() or "道")[:2],
                },
            )
            conn.commit()
        best_effort_sync()
        flash(request, "个人主页已更新。", "success")
        return RedirectResponse(request.url_for("profile_page"), status_code=303)

    @router.post("/profile/avatar", name="profile_avatar_save")
    async def profile_avatar_save(
        request: Request,
        avatar_action: str = Form("symbol"),
        avatar_choice: str = Form("道"),
        avatar_custom: str = Form(""),
        avatar: UploadFile | None = File(default=None),
    ):
        symbol = (avatar_custom.strip() or avatar_choice.strip() or "道")[:2]
        try:
            if avatar_action == "upload":
                if not avatar or not avatar.filename:
                    raise ValueError("请先选择一张头像图片。")
                data = await avatar.read(MAX_AVATAR_BYTES + 1)
                save_avatar_bytes(data)
            elif avatar_action == "symbol":
                remove_avatar()
            else:
                raise ValueError("无法识别头像操作。")
            with connect() as conn:
                conn.execute(
                    "UPDATE player_profile SET avatar_symbol=?,updated_at=? WHERE id=1",
                    (symbol, now_iso()),
                )
                queue_event(conn, "profile_updated", {"avatar_symbol": symbol})
                conn.commit()
            best_effort_sync()
            message = "头像图片已更新。" if avatar_action == "upload" else "已改用字印头像。"
            flash(request, message, "success")
        except ValueError as exc:
            flash(request, str(exc), "error")
        return RedirectResponse(
            str(request.url_for("profile_page")) + "?edit=1",
            status_code=303,
        )

    app.include_router(router)

# ==================== 炼丹 ====================





DELIVERY_DIR = STORAGE_ROOT / "deliveries"

HERB_GRADES = {
    1: {"label": "凡品", "name": "青露草", "icon": "♧"},
    2: {"label": "灵品", "name": "凝神花", "icon": "☘"},
    3: {"label": "玄品", "name": "玄脉芝", "icon": "⚘"},
    4: {"label": "地品", "name": "地心莲", "icon": "✿"},
    5: {"label": "天品", "name": "天衍果", "icon": "❋"},
}

RECIPES = {
    "clarity_pill": {
        "name": "清心丹",
        "desc": "使用后增加 10 修为，适合完成一次小复盘后收功。",
        "costs": {1: 2},
        "xp": 10,
    },
    "focus_pill": {
        "name": "凝元丹",
        "desc": "使用后增加 25 修为，象征把零散知识凝成方法。",
        "costs": {1: 1, 2: 2},
        "xp": 25,
    },
    "tribulation_pill": {
        "name": "渡劫丹",
        "desc": "只在金丹以上、修为达到下一大境界门槛时开启一次五问雷劫。",
        "costs": {3: 2, 4: 1},
        "xp": 0,
    },
    "insight_pill": {
        "name": "明悟丹",
        "desc": "把一次机制解释炼成可迁移结论，服用后增加 18 修为。",
        "costs": {1: 1, 3: 1},
        "xp": 18,
    },
    "evidence_pill": {
        "name": "证心丹",
        "desc": "奖励完成证据核对的行动，服用后增加 32 修为。",
        "costs": {2: 2, 3: 1},
        "xp": 32,
    },
    "method_pill": {
        "name": "百炼丹",
        "desc": "象征把失败复盘转化为可复用方法，服用后增加 45 修为。",
        "costs": {2: 1, 3: 2, 4: 1},
        "xp": 45,
    },
    "star_pill": {
        "name": "星衍丹",
        "desc": "用于奖励跨工作区联结与综合判断，服用后增加 60 修为。",
        "costs": {3: 2, 4: 2},
        "xp": 60,
    },
    "grand_pill": {
        "name": "太一丹",
        "desc": "高阶交付的纪念丹药，服用后增加 88 修为。",
        "costs": {4: 2, 5: 1},
        "xp": 88,
    },
    "renewal_pill": {
        "name": "回元丹",
        "desc": "完成艰难任务后的温和收功，服用后增加 20 修为。",
        "costs": {1: 2, 2: 1},
        "xp": 20,
    },
}


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", Path(value).name).strip("._")[:160] or "file"


def _pill_rows(conn) -> list[dict[str, Any]]:
    rows = {
        row["item_key"]: dict(row)
        for row in conn.execute("SELECT * FROM inventory_items WHERE item_type='pill'")
    }
    return [
        {"key": key, **spec, "quantity": int(rows.get(key, {}).get("quantity", 0))}
        for key, spec in RECIPES.items()
    ]


def register_alchemy_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    @router.get("/alchemy", response_class=HTMLResponse, name="alchemy_page")
    def alchemy_page(request: Request):
        with connect() as conn:
            tasks = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM special_tasks
                    WHERE status IN ('offered','active')
                    ORDER BY status='active' DESC,id DESC
                    """
                )
            ]
            completed = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM special_tasks WHERE status='completed' ORDER BY completed_at DESC LIMIT 8"
                )
            ]
            quantities = {
                int(row["grade"]): int(row["quantity"])
                for row in conn.execute("SELECT grade,quantity FROM herb_inventory")
            }
            herbs = [
                {"grade": grade, **spec, "quantity": quantities.get(grade, 0)}
                for grade, spec in HERB_GRADES.items()
            ]
            pills = _pill_rows(conn)
        recipes = []
        for key, spec in RECIPES.items():
            costs = [
                f"{HERB_GRADES[grade]['label']}{HERB_GRADES[grade]['name']}×{amount}"
                for grade, amount in spec["costs"].items()
            ]
            recipes.append({"key": key, **spec, "cost_text": " + ".join(costs)})
        return templates.TemplateResponse(
            request=request,
            name="alchemy.html",
            context=context(
                request,
                "alchemy",
                tasks=tasks,
                completed=completed,
                herbs=herbs,
                pills=pills,
                recipes=recipes,
                grades=HERB_GRADES,
                ai_status=provider_status(),
            ),
        )

    @router.post("/alchemy/tasks/generate", name="alchemy_task_generate")
    def task_generate(
        request: Request,
        difficulty: int = Form(2),
        focus: str = Form(""),
    ):
        difficulty = max(1, min(int(difficulty), 5))
        with connect() as conn:
            recent = [
                dict(row)
                for row in conn.execute(
                    "SELECT title,source_text FROM review_sources ORDER BY source_date DESC,id DESC LIMIT 6"
                )
            ]
            active = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT title,deliverable FROM daily_missions
                    WHERE completed=0 ORDER BY id DESC LIMIT 5
                    """
                )
            ]
        context_text = "\n".join(
            [f"用户指定：{focus.strip()}"] if focus.strip() else []
        )
        context_text += "\n" + "\n".join(f"{row['title']}：{row['source_text']}" for row in recent)
        context_text += "\n" + "\n".join(f"近期任务：{row['title']}；交付={row['deliverable']}" for row in active)
        task, provider, fallback_reason = generate_special_task(context_text, difficulty)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO special_tasks(
                    title,description,deliverable,why_it_matters,difficulty,status,
                    provider,fallback_reason,created_at
                ) VALUES (?,?,?,?,?,'offered',?,?,?)
                """,
                (
                    task["title"],
                    task["description"],
                    task["deliverable"],
                    task["why_it_matters"],
                    difficulty,
                    provider,
                    fallback_reason[:1000],
                    now_iso(),
                ),
            )
            conn.commit()
        flash(request, f"已生成 {HERB_GRADES[difficulty]['label']} 特殊任务。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    @router.post("/alchemy/tasks/{task_id}/accept", name="alchemy_task_accept")
    def task_accept(request: Request, task_id: int):
        with connect() as conn:
            active = conn.execute("SELECT COUNT(*) n FROM special_tasks WHERE status='active'").fetchone()["n"]
            if active:
                flash(request, "先完成或放弃当前特殊任务，再接下一项。", "error")
                return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
            conn.execute(
                "UPDATE special_tasks SET status='active',accepted_at=? WHERE id=? AND status='offered'",
                (now_iso(), task_id),
            )
            conn.commit()
        flash(request, "特殊任务已接取。完成真实交付后才能获得灵草。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    @router.post("/alchemy/tasks/{task_id}/abandon", name="alchemy_task_abandon")
    def task_abandon(request: Request, task_id: int):
        with connect() as conn:
            conn.execute(
                "UPDATE special_tasks SET status='abandoned' WHERE id=? AND status IN ('offered','active')",
                (task_id,),
            )
            conn.commit()
        flash(request, "任务已放下，不扣资源，也不形成补课债务。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    @router.post("/alchemy/tasks/{task_id}/complete", name="alchemy_task_complete")
    def task_complete(
        request: Request,
        task_id: int,
        evidence: str = Form(...),
        review_text: str = Form(...),
        files: list[UploadFile] = File(default=[]),
    ):
        evidence = evidence.strip()
        review_text = review_text.strip()
        if not evidence or not review_text:
            flash(request, "请同时填写交付说明和复盘关键文本。", "error")
            return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
        with connect() as conn:
            task = conn.execute("SELECT * FROM special_tasks WHERE id=? AND status='active'", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404)

        storage_key = f"special_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        root = DELIVERY_DIR / storage_key
        root.mkdir(parents=True, exist_ok=True)
        try:
            (root / "交付说明.txt").write_text(evidence, encoding="utf-8")
            (root / "复盘关键文本.txt").write_text(review_text, encoding="utf-8")
            used_bytes = 0
            for upload in [item for item in files if item.filename]:
                target = root / _safe_name(upload.filename or "file")
                used_bytes += save_upload_limited(
                    upload,
                    target,
                    remaining_bytes=MAX_UPLOAD_BYTES - used_bytes,
                )
            ts = now_iso()
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE special_tasks
                    SET status='completed',evidence=?,review_text=?,storage_key=?,completed_at=?
                    WHERE id=? AND status='active'
                    """,
                    (evidence, review_text, storage_key, ts, task_id),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO review_sources(
                        source_type,source_id,title,source_text,storage_key,source_date,created_at
                    ) VALUES ('special_task',?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        task["title"],
                        review_text,
                        storage_key,
                        ts[:10],
                        ts,
                    ),
                )
                add_or_increment_herb(conn, int(task["difficulty"]))
                conn.execute(
                    "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                    (
                        "special_task",
                        10 + int(task["difficulty"]) * 5,
                        f"完成特殊任务：{task['title']}",
                        ts,
                    ),
                )
                conn.commit()
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        herb = HERB_GRADES[int(task["difficulty"])]
        flash(request, f"交付成立，获得 {herb['label']}{herb['name']} ×1。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    @router.post("/alchemy/craft/{recipe_key}", name="alchemy_craft")
    def alchemy_craft(request: Request, recipe_key: str):
        recipe = RECIPES.get(recipe_key)
        if not recipe:
            raise HTTPException(status_code=404)
        with connect() as conn:
            quantities = {
                int(row["grade"]): int(row["quantity"])
                for row in conn.execute("SELECT grade,quantity FROM herb_inventory")
            }
            missing = [
                grade
                for grade, amount in recipe["costs"].items()
                if quantities.get(grade, 0) < amount
            ]
            if missing:
                flash(request, "灵草不足，先完成对应等级的特殊任务。", "error")
                return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
            ts = now_iso()
            for grade, amount in recipe["costs"].items():
                conn.execute(
                    "UPDATE herb_inventory SET quantity=quantity-?,updated_at=? WHERE grade=?",
                    (amount, ts, grade),
                )
            conn.execute("DELETE FROM herb_inventory WHERE quantity<=0")
            conn.execute(
                """
                INSERT INTO inventory_items(
                    item_key,item_type,quantity,level,equipped,acquired_at,updated_at
                ) VALUES (?,'pill',1,1,0,?,?)
                ON CONFLICT(item_key) DO UPDATE SET
                    quantity=inventory_items.quantity+1,updated_at=excluded.updated_at
                """,
                (recipe_key, ts, ts),
            )
            conn.execute(
                "UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='first_pill'",
                (ts,),
            )
            crafted_types = conn.execute(
                "SELECT COUNT(*) n FROM inventory_items WHERE item_type='pill' AND quantity>0"
            ).fetchone()["n"]
            if int(crafted_types) >= 5:
                conn.execute(
                    "UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='five_recipes'",
                    (ts,),
                )
            conn.commit()
        flash(request, f"{recipe['name']}炼制成功。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    @router.post("/alchemy/pills/{pill_key}/use", name="alchemy_pill_use")
    def pill_use(request: Request, pill_key: str):
        recipe = RECIPES.get(pill_key)
        if not recipe or not int(recipe["xp"]):
            flash(request, "这枚丹药不能在这里直接使用。", "error")
            return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
        with connect() as conn:
            row = conn.execute(
                "SELECT quantity FROM inventory_items WHERE item_key=? AND item_type='pill'",
                (pill_key,),
            ).fetchone()
            if not row or int(row["quantity"]) < 1:
                flash(request, "你还没有这枚丹药。", "error")
                return RedirectResponse(request.url_for("alchemy_page"), status_code=303)
            ts = now_iso()
            conn.execute(
                "UPDATE inventory_items SET quantity=quantity-1,updated_at=? WHERE item_key=?",
                (ts, pill_key),
            )
            conn.execute("DELETE FROM inventory_items WHERE item_key=? AND quantity<=0", (pill_key,))
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("pill_use", int(recipe["xp"]), f"服用{recipe['name']}", ts),
            )
            conn.commit()
        flash(request, f"服用{recipe['name']}，增加 {recipe['xp']} 修为。", "success")
        return RedirectResponse(request.url_for("alchemy_page"), status_code=303)

    app.include_router(router)

# ==================== 休闲空间 ====================





def register_idle_routes(app, templates, context, flash):
    router = APIRouter()

    @router.get("/idle", response_class=HTMLResponse, name="idle_page")
    def idle_page(request: Request):
        layout = get_setting("idle_layout", "vertical")
        if layout not in {"vertical", "horizontal"}:
            layout = "vertical"
        builtin_key = get_setting("idle_wallpaper_builtin", "dawn")
        return templates.TemplateResponse(
            request=request,
            name="idle.html",
            context=context(
                request,
                "idle",
                idle_layout=layout,
                idle_wallpaper_url=wallpaper_url(),
                idle_wallpaper_kind=get_setting("idle_wallpaper_kind", "builtin"),
                idle_wallpaper_file=current_wallpaper_filename(),
                builtin_wallpapers=BUILTIN_WALLPAPERS,
                custom_wallpapers=custom_wallpapers(),
                selected_builtin= builtin_key if builtin_key in BUILTIN_WALLPAPERS else "dawn",
                idle_focus=get_setting("idle_focus", "安静积累，下一步只做一件最重要的事。")[:160],
            ),
        )

    @router.post("/idle/config", name="idle_config_save")
    def idle_config_save(
        request: Request,
        layout: str = Form("vertical"),
        focus: str = Form(""),
        builtin_wallpaper: str = Form(""),
        custom_wallpaper: str = Form(""),
    ):
        set_setting("idle_layout", layout if layout in {"vertical", "horizontal"} else "vertical")
        set_setting("idle_focus", focus.strip()[:160] or "安静积累，下一步只做一件最重要的事。")
        if builtin_wallpaper:
            try:
                select_builtin_wallpaper(builtin_wallpaper)
            except ValueError as exc:
                flash(request, str(exc), "error")
                return RedirectResponse(request.url_for("idle_page"), status_code=303)
        if custom_wallpaper:
            try:
                select_custom_wallpaper(custom_wallpaper)
            except ValueError as exc:
                flash(request, str(exc), "error")
                return RedirectResponse(request.url_for("idle_page"), status_code=303)
        flash(request, "挂机界面已保存：只记录专注时间，不会自动增加修为或资产。")
        return RedirectResponse(request.url_for("idle_page"), status_code=303)

    @router.post("/idle/wallpaper", name="idle_wallpaper_upload")
    async def idle_wallpaper_upload(
        request: Request,
        file: UploadFile = File(...),
    ):
        try:
            save_wallpaper_bytes(await file.read())
        except ValueError as exc:
            flash(request, str(exc), "error")
        else:
            flash(request, "自定义壁纸已保存到本机用户数据，不会上传同行会。")
        return RedirectResponse(request.url_for("idle_page"), status_code=303)

    app.include_router(router)
