from __future__ import annotations

import json
import uuid
from typing import Any, Callable
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services.workspace_profiles import (
    WORKSPACE_ACCENTS,
    WORKSPACE_MODULES,
    WORKSPACE_TOOLS,
    normalize_toolset,
    normalize_workflow,
    profile_for,
)


def _workspace_stats(conn, workspace_id: int) -> dict[str, int]:
    return {
        "papers": int(conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE workspace_id=? AND status='active'",
            (workspace_id,),
        ).fetchone()["n"]),
        "notes": int(conn.execute(
            """
            SELECT COUNT(*) n FROM entries
            WHERE workspace_id=? AND status='active'
              AND kind IN ('note','question','idea','failure','sop','code')
            """,
            (workspace_id,),
        ).fetchone()["n"]),
        "uploads": int(conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE workspace_id=? AND file_path IS NOT NULL",
            (workspace_id,),
        ).fetchone()["n"]),
        "tasks": int(conn.execute(
            "SELECT COUNT(*) n FROM quests WHERE workspace_id=? AND completed=0",
            (workspace_id,),
        ).fetchone()["n"]),
        "experiments": int(conn.execute(
            "SELECT COUNT(*) n FROM experiments WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()["n"]),
        "simulations": int(conn.execute(
            "SELECT COUNT(*) n FROM simulations WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()["n"]),
        "datasets": int(conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE workspace_id=? AND kind='dataset' AND status='active'",
            (workspace_id,),
        ).fetchone()["n"]),
        "folders": int(conn.execute("SELECT COUNT(*) n FROM research_folders").fetchone()["n"]),
        "focus": 0,
    }


def _record_count(stats: dict[str, int], module: str) -> int:
    if module == "experiments":
        return stats["experiments"]
    if module in {"simulations", "md"}:
        return stats["simulations"]
    if module == "datasets":
        return stats["datasets"]
    return stats["papers"]


def _workspace_item(row: Any, stats: dict[str, int]) -> dict[str, Any]:
    item = dict(row)
    module = str(item["module"])
    item["record_count"] = _record_count(stats, module)
    item["module_label"] = WORKSPACE_MODULES.get(module, WORKSPACE_MODULES["knowledge"])[0]
    item["workflow"] = normalize_workflow(item.get("workflow_json", "[]"), module)
    item["toolset"] = normalize_toolset(item.get("toolset_json", "[]"), module)
    item["tool_labels"] = [WORKSPACE_TOOLS[key][0] for key in item["toolset"]]
    item["profile"] = profile_for(module)
    return item


def _tool_url(request: Request, key: str, workspace: dict[str, Any]) -> str:
    workspace_id = int(workspace["id"])
    if key == "papers":
        return f"{request.url_for('library')}?workspace={workspace_id}"
    if key == "notes":
        return f"{request.url_for('note_new')}?workspace_id={workspace_id}"
    if key == "uploads":
        return f"{request.url_for('upload')}?workspace_id={workspace_id}"
    if key == "tasks":
        return f"{request.url_for('cultivation_page')}?workspace={workspace_id}"
    if key == "experiments":
        return f"{request.url_for('experiments_page')}?workspace={workspace_id}"
    if key == "simulations":
        return f"{request.url_for('simulations_page')}?workspace={workspace_id}"
    if key == "datasets":
        return f"{request.url_for('datasets_page')}?workspace={workspace_id}"
    if key == "folders":
        return str(request.url_for("folders_page"))
    if key == "focus":
        return f"{request.url_for('retreat_page')}?focus={quote_plus(str(workspace['name']))}"
    return str(request.url_for("library"))


def _primary_action(request: Request, workspace: dict[str, Any]) -> dict[str, str]:
    module = str(workspace["module"])
    mapping = {
        "experiments": ("打开实验台账", "experiments"),
        "simulations": ("打开 LAMMPS 案例", "simulations"),
        "md": ("打开 MD 案例", "simulations"),
        "datasets": ("打开数据档案", "datasets"),
        "ml": ("写运行/模型卡", "notes"),
        "comsol": ("写模型记录", "notes"),
        "knowledge": ("写专题笔记", "notes"),
    }
    label, tool = mapping.get(module, mapping["knowledge"])
    return {"label": label, "url": _tool_url(request, tool, workspace)}


def _recent_records(conn, request: Request, workspace: dict[str, Any]) -> list[dict[str, str]]:
    workspace_id = int(workspace["id"])
    module = str(workspace["module"])
    if module == "experiments":
        return [
            {
                "mark": "验",
                "title": str(row["sample_id"]),
                "meta": f"{row['status']} · {row['title'] or '未命名实验'}",
                "url": f"{request.url_for('experiments_page')}?workspace={workspace_id}&edit={row['id']}",
            }
            for row in conn.execute(
                "SELECT id,sample_id,title,status FROM experiments WHERE workspace_id=? ORDER BY updated_at DESC LIMIT 5",
                (workspace_id,),
            )
        ]
    if module in {"simulations", "md"}:
        return [
            {
                "mark": "算",
                "title": str(row["case_name"]),
                "meta": f"{row['status']} · {row['engine']} {row['engine_version'] or ''}".strip(),
                "url": f"{request.url_for('simulations_page')}?workspace={workspace_id}",
            }
            for row in conn.execute(
                "SELECT case_name,status,engine,engine_version FROM simulations WHERE workspace_id=? ORDER BY updated_at DESC LIMIT 5",
                (workspace_id,),
            )
        ]
    return [
        {
            "mark": "数" if row["kind"] == "dataset" else "文",
            "title": str(row["title"]),
            "meta": f"{row['kind']} · {str(row['updated_at'])[:10]}",
            "url": str(request.url_for("entry_view", entry_id=row["id"])),
        }
        for row in conn.execute(
            """
            SELECT id,title,kind,updated_at FROM entries
            WHERE workspace_id=? AND status='active'
            ORDER BY updated_at DESC LIMIT 5
            """,
            (workspace_id,),
        )
    ]


def _pin_available(conn, workspace_id: int = 0) -> bool:
    count = int(conn.execute(
        "SELECT COUNT(*) n FROM workspaces WHERE active=1 AND pinned_home=1 AND id!=?",
        (workspace_id,),
    ).fetchone()["n"])
    return count < 6


def register_workspace_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
    entry_dict: Callable[[Any], dict[str, Any]],
):
    router = APIRouter()

    @router.get("/workspaces", response_class=HTMLResponse, name="workspaces_page")
    def workspaces_page(request: Request):
        with connect() as conn:
            items = []
            for row in conn.execute("SELECT * FROM workspaces ORDER BY active DESC,sort_order,id"):
                stats = _workspace_stats(conn, int(row["id"]))
                items.append(_workspace_item(row, stats))
        return templates.TemplateResponse(
            request=request,
            name="workspaces.html",
            context=context(
                request,
                "workspaces",
                items=items,
                workspace_modules=WORKSPACE_MODULES,
                workspace_tools=WORKSPACE_TOOLS,
                workspace_accents=WORKSPACE_ACCENTS,
            ),
        )

    @router.post("/workspaces/new", name="workspace_new")
    def workspace_new(
        request: Request,
        name: str = Form(...),
        icon: str = Form("研"),
        module: str = Form("knowledge"),
        description: str = Form(""),
        pinned_home: str = Form(""),
    ):
        title = name.strip()[:40]
        if not title:
            flash(request, "工作区名称不能为空。", "error")
            return RedirectResponse(request.url_for("workspaces_page"), status_code=303)
        module_value = module if module in WORKSPACE_MODULES else "knowledge"
        profile = profile_for(module_value)
        ts = now_iso()
        with connect() as conn:
            pin = 1 if pinned_home == "1" and _pin_available(conn) else 0
            order = int(conn.execute(
                "SELECT COALESCE(MAX(sort_order),0)+10 n FROM workspaces"
            ).fetchone()["n"])
            conn.execute(
                """
                INSERT INTO workspaces(
                    workspace_key,name,icon,module,description,accent,sort_order,active,pinned_home,
                    objective,workflow_json,toolset_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,'clay',?,1,?,?,?,?,?,?)
                """,
                (
                    uuid.uuid4().hex,
                    title,
                    icon.strip()[:2] or "研",
                    module_value,
                    description.strip()[:240],
                    order,
                    pin,
                    profile["objective"],
                    json.dumps(profile["workflow"], ensure_ascii=False),
                    json.dumps(profile["tools"], ensure_ascii=False),
                    ts,
                    ts,
                ),
            )
            conn.commit()
        flash(request, f"已建立工作区「{title}」，并装配该类型的默认流程。", "success")
        return RedirectResponse(request.url_for("workspaces_page"), status_code=303)

    @router.post("/workspaces/{workspace_id}/save", name="workspace_save")
    def workspace_save(
        request: Request,
        workspace_id: int,
        name: str = Form(...),
        icon: str = Form("研"),
        module: str = Form("knowledge"),
        description: str = Form(""),
        objective: str = Form(""),
        workflow: str = Form(""),
        tools: list[str] = Form(default=[]),
        accent: str = Form("clay"),
        sort_order: int = Form(0),
        active: str = Form(""),
        pinned_home: str = Form(""),
    ):
        title = name.strip()[:40]
        if not title:
            flash(request, "工作区名称不能为空。", "error")
            return RedirectResponse(request.url_for("workspaces_page"), status_code=303)
        module_value = module if module in WORKSPACE_MODULES else "knowledge"
        active_value = 1 if active == "1" else 0
        with connect() as conn:
            row = conn.execute("SELECT id FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404)
            pin_requested = pinned_home == "1" and active_value == 1
            if pin_requested and not _pin_available(conn, workspace_id):
                flash(request, "首页最多固定 6 个工作区；请先取消另一个首页固定。", "error")
                return RedirectResponse(request.url_for("workspaces_page"), status_code=303)
            workflow_value = normalize_workflow(workflow.splitlines(), module_value)
            toolset_value = normalize_toolset(tools, module_value)
            conn.execute(
                """
                UPDATE workspaces
                SET name=?,icon=?,module=?,description=?,objective=?,workflow_json=?,toolset_json=?,
                    accent=?,sort_order=?,active=?,pinned_home=?,updated_at=?
                WHERE id=?
                """,
                (
                    title,
                    icon.strip()[:2] or "研",
                    module_value,
                    description.strip()[:240],
                    objective.strip()[:300] or profile_for(module_value)["objective"],
                    json.dumps(workflow_value, ensure_ascii=False),
                    json.dumps(toolset_value, ensure_ascii=False),
                    accent if accent in WORKSPACE_ACCENTS else "clay",
                    max(-999, min(int(sort_order or 0), 9999)),
                    active_value,
                    1 if pin_requested else 0,
                    now_iso(),
                    workspace_id,
                ),
            )
            conn.commit()
        flash(request, "工作区的流程、组件和显示设置已保存。", "success")
        return RedirectResponse(request.url_for("workspaces_page"), status_code=303)

    @router.post("/workspaces/{workspace_id}/reset", name="workspace_reset")
    def workspace_reset(request: Request, workspace_id: int):
        with connect() as conn:
            row = conn.execute("SELECT module FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404)
            module = str(row["module"])
            profile = profile_for(module)
            conn.execute(
                """
                UPDATE workspaces
                SET objective=?,workflow_json=?,toolset_json=?,updated_at=?
                WHERE id=?
                """,
                (
                    profile["objective"],
                    json.dumps(profile["workflow"], ensure_ascii=False),
                    json.dumps(profile["tools"], ensure_ascii=False),
                    now_iso(),
                    workspace_id,
                ),
            )
            conn.commit()
        flash(request, "已恢复该类型的推荐流程与组件组合。", "success")
        return RedirectResponse(request.url_for("workspaces_page"), status_code=303)

    @router.get("/workspaces/{workspace_id}", response_class=HTMLResponse, name="workspace_open")
    def workspace_open(request: Request, workspace_id: int):
        with connect() as conn:
            row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404)
            stats = _workspace_stats(conn, workspace_id)
            workspace = _workspace_item(row, stats)
            tool_cards = [
                {
                    "key": key,
                    "label": WORKSPACE_TOOLS[key][0],
                    "mark": WORKSPACE_TOOLS[key][1],
                    "description": WORKSPACE_TOOLS[key][2],
                    "count": stats.get(key, 0),
                    "url": _tool_url(request, key, workspace),
                }
                for key in workspace["toolset"]
            ]
            recent_records = _recent_records(conn, request, workspace)
        return templates.TemplateResponse(
            request=request,
            name="workspace.html",
            context=context(
                request,
                "workspace",
                workspace=workspace,
                workspace_guide=workspace["profile"],
                workspace_stats=stats,
                tool_cards=tool_cards,
                recent_records=recent_records,
                primary_action=_primary_action(request, workspace),
                active_workspace_id=workspace_id,
            ),
        )

    app.include_router(router)
