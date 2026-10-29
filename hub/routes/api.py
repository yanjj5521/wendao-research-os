from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from hub.common import (
    profile_payload,
    BASE_DIR,
    VERSION,
    api_user,
    audit,
    parse_theme,
)
from hub.sync import apply_event
from hub.db import (
    HUB_RELEASE_DIR,
    connect_hub,
    hub_transaction,
    now_iso,
)

router = APIRouter()
MAX_API_BODY_BYTES = 256 * 1024


async def _json_body(request: Request) -> object:
    raw = await request.body()
    if len(raw) > MAX_API_BODY_BYTES:
        raise HTTPException(status_code=413, detail="请求体过大")
    try:
        return json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON格式无效") from exc


@router.get("/api/v1/ping")
def api_ping():
    return {"status": "ok", "version": VERSION, "mode": "coordination-hub"}


@router.post("/feedback", name="hub_feedback_submit")
async def feedback_submit(request: Request):
    """成员反馈通道：Bearer Token 认证；每账户每天最多 10 条，内容 ≤4000 字。"""
    user = api_user(request)
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    kind = str(body.get("kind", "bug"))
    content = str(body.get("content", "")).strip()
    contact = str(body.get("contact", "")).strip()[:160]
    if kind not in {"bug", "idea", "other"}:
        kind = "bug"
    if len(content) < 10:
        raise HTTPException(status_code=400, detail="反馈内容至少 10 个字。")
    if len(content) > 4000:
        raise HTTPException(status_code=400, detail="反馈内容过长（不超过 4000 字）。")
    today = now_iso()[:10]
    with hub_transaction() as conn:
        today_count = int(
            conn.execute(
                "SELECT COUNT(*) n FROM hub_feedback WHERE account_id=? AND substr(created_at,1,10)=?",
                (int(user["id"]), today),
            ).fetchone()["n"]
        )
        if today_count >= 10:
            raise HTTPException(status_code=429, detail="今天的反馈已达 10 条上限，明天再来。")
        conn.execute(
            "INSERT INTO hub_feedback(account_id,kind,content,contact,created_at) VALUES (?,?,?,?,?)",
            (int(user["id"]), kind, content, contact, now_iso()),
        )
        audit(conn, int(user["id"]), "feedback", f"{kind} · {len(content)} 字", request)
    return {"status": "accepted"}


@router.get("/api/v1/bootstrap")
def api_bootstrap(request:Request):
    user=api_user(request)
    with connect_hub() as conn:
        return profile_payload(conn, int(user["id"]))


@router.post("/api/v1/events")
async def api_events(request:Request):
    user=api_user(request)
    body=await _json_body(request)
    events=body.get("events",[]) if isinstance(body,dict) else []
    if not isinstance(events, list) or len(events) > 100:
        raise HTTPException(status_code=400, detail="事件列表无效")
    results=[]
    with hub_transaction() as conn:
        for event in events:
            results.append(apply_event(conn, int(user["id"]), event if isinstance(event, dict) else {}))
        audit(conn,int(user["id"]),"api_sync",f"{len(events)} events",request)
        payload=profile_payload(conn,int(user["id"]))
    return {"results":results,"state":payload}


@router.post("/api/v1/personalization")
async def api_personalization(request:Request):
    user = api_user(request)
    body = await _json_body(request)
    theme = parse_theme(body if isinstance(body, dict) else {})
    with hub_transaction() as conn:
        conn.execute("UPDATE account_profiles SET theme_json=?,revision=revision+1,updated_at=? WHERE account_id=?",(json.dumps(theme,ensure_ascii=False),now_iso(),user["id"]))
    return {"ok":True,"theme":theme}


@router.get("/api/v1/releases/latest")
def api_latest_release(request:Request):
    api_user(request)
    with connect_hub() as conn:
        row=conn.execute("SELECT id,version,title,notes,file_size,sha256,created_at FROM hub_releases WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    return {"release":dict(row) if row else None}


@router.get("/api/v1/releases/{release_id}/download")
def api_release_download(request:Request,release_id:int):
    api_user(request)
    with connect_hub() as conn:
        row = conn.execute("SELECT * FROM hub_releases WHERE id=? AND active=1", (release_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    stored = Path(str(row["file_path"]))
    target=(stored if stored.is_absolute() else BASE_DIR/stored).resolve()
    if not target.exists() or HUB_RELEASE_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=404)
    return FileResponse(target,filename=target.name,media_type="application/zip")
