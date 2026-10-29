from __future__ import annotations

import json

from fastapi import APIRouter

from services.career import career_snapshot
from web.runtime import (
    APP_VERSION,
    Any,
    BASE_DIR,
    DATA_ROOT,
    Form,
    HTMLResponse,
    Request,
    achievements,
    activity_streak,
    asset_transact,
    connect,
    context,
    current_realm,
    datetime,
    flash,
    get_setting,
    now_iso,
    pending_review_group,
    redirect,
    set_setting,
    templates,
    total_xp,
)

app = APIRouter()

@app.get("/achievements", response_class=HTMLResponse, name="achievements_page")
def achievements_page(request: Request):
    with connect() as conn:
        items = achievements(conn)
        egg_items = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM easter_eggs ORDER BY unlocked DESC,discovered_at,title"
            )
        ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(item["category"], []).append(item)
    unlocked_count = sum(1 for item in items if item["unlocked"])
    return templates.TemplateResponse(
        request=request,
        name="achievements.html",
        context=context(
            request,
            "achievements",
            achievements=items,
            achievement_groups=groups,
            unlocked_count=unlocked_count,
            overall_progress=round(unlocked_count / max(len(items), 1) * 100),
            easter_eggs=egg_items,
            eggs_unlocked=sum(1 for egg in egg_items if egg["unlocked"]),
        ),
    )

@app.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request):
    with connect() as conn:
        stats = {
            "total": conn.execute("SELECT COUNT(*) n FROM entries").fetchone()["n"],
            "documents": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind='document'").fetchone()["n"],
            "notes": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind IN ('note','question','idea','failure','sop')").fetchone()["n"],
            "datasets": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind='dataset'").fetchone()["n"],
            "experiments": conn.execute("SELECT COUNT(*) n FROM entries WHERE kind='experiment'").fetchone()["n"],
        }
        profile = dict(conn.execute("SELECT * FROM player_profile WHERE id=1").fetchone())
        career_next = career_snapshot(conn).get("next_step")
        xp = total_xp(conn)
        realm = current_realm(xp)
        streak = activity_streak(conn)
        home_workspaces = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id,workspace_key,name,icon,module,accent
                FROM workspaces
                WHERE active=1 AND pinned_home=1
                ORDER BY sort_order,id
                LIMIT 6
                """
            )
        ]
        if not home_workspaces:
            home_workspaces = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id,workspace_key,name,icon,module,accent
                    FROM workspaces WHERE active=1 ORDER BY sort_order,id LIMIT 6
                    """
                )
            ]
        workspace_total = int(conn.execute(
            "SELECT COUNT(*) n FROM workspaces WHERE active=1"
        ).fetchone()["n"])
        # The chip row answers "what am I learning" at a glance, so it lists every
        # active domain, not only the six pinned to the workbench dock.
        active_project_count = int(conn.execute(
            "SELECT COUNT(*) n FROM research_projects WHERE status='active'"
        ).fetchone()["n"])
        last_activity = conn.execute(
            "SELECT detail,created_at FROM activities ORDER BY id DESC LIMIT 1"
        ).fetchone()

        pending_review = pending_review_group(conn) if get_setting("review_popup", "1") == "1" else None
        raw_layout = get_setting("home_layout", "[]")
        try:
            saved_layout = json.loads(raw_layout)
        except (TypeError, json.JSONDecodeError):
            saved_layout = []
        allowed_home_blocks = {"gate", "search", "shortcuts", "workbench", "continuity"}
        home_layout = []
        seen = set()
        for item in saved_layout if isinstance(saved_layout, list) else []:
            key = str(item.get("key", "")) if isinstance(item, dict) else ""
            if key not in allowed_home_blocks or key in seen:
                continue
            try:
                span = max(4, min(12, int(item.get("span", 12))))
            except (TypeError, ValueError):
                span = 12
            home_layout.append({"key": key, "span": span})
            seen.add(key)
        for key in ("gate", "search", "shortcuts", "workbench", "continuity"):
            if key not in seen:
                home_layout.append({"key": key, "span": 12})
        data = context(
            request,
            "dashboard",
            stats=stats,
            xp=xp,
            realm=realm,
            streak=streak,
            pending_review=pending_review,
            career_next=career_next,
            profile=profile,
            home_workspaces=home_workspaces,
            home_workspace_total=workspace_total,
            home_project_count=active_project_count,
            home_last_saved=(
                str(last_activity["detail"])[:52]
                if last_activity and str(last_activity["detail"]).strip()
                else "尚未留下第一份科研证据"
            ),
            home_data_label=(
                "独立用户数据目录"
                if DATA_ROOT.resolve() != BASE_DIR.resolve()
                else "当前目录本地保存"
            ),
            app_version=APP_VERSION,
            today_label=datetime.now().strftime("%Y年%m月%d日"),
            home_layout=home_layout,
            home_layout_json=json.dumps(home_layout, ensure_ascii=False),
            home_layout_map={item["key"]: {"order": index + 1, "span": item["span"]} for index, item in enumerate(home_layout)},
        )
    return templates.TemplateResponse(request=request, name="dashboard.html", context=data)


@app.post("/home/layout", name="home_layout_save")
def home_layout_save(request: Request, layout: str = Form("[]")):
    allowed = {"gate", "search", "shortcuts", "workbench", "continuity"}
    try:
        raw = json.loads(layout)
    except json.JSONDecodeError:
        raw = []
    normalized = []
    seen = set()
    for item in raw if isinstance(raw, list) else []:
        key = str(item.get("key", "")) if isinstance(item, dict) else ""
        if key not in allowed or key in seen:
            continue
        try:
            span = max(4, min(12, int(item.get("span", 12))))
        except (TypeError, ValueError):
            span = 12
        normalized.append({"key": key, "span": span})
        seen.add(key)
    for key in ("gate", "search", "shortcuts", "workbench", "continuity"):
        if key not in seen:
            normalized.append({"key": key, "span": 12})
    set_setting("home_layout", json.dumps(normalized, ensure_ascii=False))
    with connect() as conn:
        conn.execute(
            "UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='home_architect'",
            (now_iso(),),
        )
        conn.commit()
    flash(request, "主页布局已保存。", "success")
    return redirect("dashboard", request)


CONSTELLATION_ECHOES = (
    "星图回响：证据之间的连线，往往比孤立的结论更重要。",
    "星图回响：如果删掉最漂亮的一张图，当前判断还站得住吗？",
    "星图回响：哪一个未测量变量，最可能让这条关系消失？",
    "星图回响：你看到的是机制，还是恰好同向变化的两个结果？",
)

PRISM_ECHOES = (
    "棱镜反问：如果结论完全相反，哪条证据最难解释？",
    "棱镜反问：把当前基线换掉，优势是否仍然存在？",
    "棱镜反问：如果异常组才是真相，主流组可能漏掉了什么？",
    "棱镜反问：哪个边界条件一改变，就会让这套解释失效？",
    "棱镜反问：能否设计一个结果，让你主动放弃当前假设？",
)


@app.post("/eggs/constellation", name="constellation_egg")
def constellation_egg(request: Request):
    with connect() as conn:
        row = conn.execute(
            "SELECT unlocked FROM easter_eggs WHERE egg_key='constellation'"
        ).fetchone()
        if row and not int(row["unlocked"] or 0):
            conn.execute(
                """
                UPDATE easter_eggs
                SET unlocked=1,discovered_at=?
                WHERE egg_key='constellation'
                """,
                (now_iso(),),
            )
            asset_transact(conn, "star_sand", 2, "发现隐藏彩蛋：几何星图")
            conn.commit()
            flash(
                request,
                "你连接了山门星图的隐藏节点：获得 2 星砂。连接本身，也是一种发现。",
                "success",
            )
        else:
            prism_active = bool(
                conn.execute(
                    """
                    SELECT 1 FROM inventory_items
                    WHERE item_key='prism_lens' AND item_type='artifact' AND equipped=1
                    """
                ).fetchone()
            )
            index_row = conn.execute(
                "SELECT value FROM settings WHERE key='constellation_echo_index'"
            ).fetchone()
            try:
                echo_index = int(index_row["value"]) if index_row else 0
            except (TypeError, ValueError):
                echo_index = 0
            echoes = PRISM_ECHOES if prism_active else CONSTELLATION_ECHOES
            message = echoes[echo_index % len(echoes)]
            conn.execute(
                """
                INSERT INTO settings(key,value) VALUES ('constellation_echo_index',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(echo_index + 1),),
            )
            conn.commit()
            flash(
                request,
                message,
                "success",
            )
    return redirect("dashboard", request)


@app.get("/retreat", response_class=HTMLResponse, name="retreat_page")
def retreat_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="retreat.html",
        context=context(request, "retreat"),
    )
