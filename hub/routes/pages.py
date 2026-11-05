from __future__ import annotations

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from hub.common import (
    current_user,
    hub_context,
    require_user,
    templates,
)
from hub.db import (
    realm_label,
    connect_hub,
    get_user_by_token,
)
from hub.releases import (
    MISSING_MESSAGE,
    release_download_response,
    resolve_release_file,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse, name="hub_home")
def home(request: Request):
    user = current_user(request)
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=hub_context(request, "login"),
        )
    with connect_hub() as conn:
        profile = dict(conn.execute("SELECT * FROM account_profiles WHERE account_id=?", (user["id"],)).fetchone())
        members = []
        for row in conn.execute(
            """SELECT u.id,u.username,u.display_name,u.last_seen_at,p.title,p.bio,p.avatar_symbol,p.skills,p.capabilities,
                      (SELECT COALESCE(SUM(t.amount),0) FROM hub_asset_transactions t
                        WHERE t.user_id=u.id AND t.asset_key='cultivation') AS cultivation
               FROM accounts u JOIN account_profiles p ON p.account_id=u.id WHERE u.active=1 AND p.public=1 ORDER BY u.role='admin' DESC,u.display_name"""
        ):
            item = dict(row)
            item["realm"] = realm_label(item["cultivation"])
            item["skills_list"] = [x.strip() for x in item["skills"].splitlines() if x.strip()][:5]
            members.append(item)
        release = conn.execute("SELECT * FROM hub_releases WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
        recent = [dict(row) for row in conn.execute(
            """SELECT e.event_type,e.created_at,u.display_name FROM hub_sync_events e JOIN accounts u ON u.id=e.user_id
               ORDER BY e.id DESC LIMIT 12"""
        )]
        resource_cards = [dict(row) for row in conn.execute(
            """SELECT c.id,c.title,c.summary,c.tags,c.source_url,c.created_at,u.display_name,p.avatar_symbol
               FROM hub_resource_cards c JOIN accounts u ON u.id=c.user_id JOIN account_profiles p ON p.account_id=u.id
               WHERE u.active=1 AND c.visibility='team' ORDER BY c.id DESC LIMIT 6"""
        )]
        social_counts = {
            "forum": int(conn.execute("SELECT COUNT(*) n FROM hub_forum_posts").fetchone()["n"]),
            "chat": int(conn.execute("SELECT COUNT(*) n FROM hub_chat_messages WHERE created_at>=?", ((datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds"),)).fetchone()["n"]),
            "offers": int(conn.execute("SELECT COUNT(*) n FROM hub_social_transfers WHERE recipient_id=? AND status='open'", (user["id"],)).fetchone()["n"]),
        }
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context=hub_context(request, "home", profile=profile, members=members, latest_release=dict(release) if release else None, recent=recent, resource_cards=resource_cards, social_counts=social_counts),
    )

@router.get("/members", response_class=HTMLResponse, name="hub_members")
def members_page(request: Request):
    require_user(request)
    with connect_hub() as conn:
        members = []
        for row in conn.execute(
            """SELECT u.id,u.username,u.display_name,u.role,u.last_seen_at,p.title,p.bio,p.skills,p.capabilities,p.avatar_symbol,
                      (SELECT COALESCE(SUM(t.amount),0) FROM hub_asset_transactions t
                        WHERE t.user_id=u.id AND t.asset_key='cultivation') AS cultivation
               FROM accounts u JOIN account_profiles p ON p.account_id=u.id WHERE u.active=1 AND p.public=1 ORDER BY u.display_name"""
        ):
            item = dict(row)
            item["realm"] = realm_label(item["cultivation"])
            item["skills_list"] = [x.strip() for x in item["skills"].splitlines() if x.strip()]
            item["capabilities_list"] = [x.strip() for x in item["capabilities"].splitlines() if x.strip()]
            members.append(item)
    return templates.TemplateResponse(request=request, name="members.html", context=hub_context(request, "members", members=members))

@router.get("/resources", response_class=HTMLResponse, name="hub_resources")
def resources_page(request: Request, q: str = ""):
    require_user(request)
    query = q.strip()[:100]
    with connect_hub() as conn:
        if query:
            like = f"%{query}%"
            rows = conn.execute(
                """SELECT c.*,u.display_name,u.username,p.avatar_symbol
                   FROM hub_resource_cards c
                   JOIN accounts u ON u.id=c.user_id
                   JOIN account_profiles p ON p.account_id=u.id
                   WHERE u.active=1 AND c.visibility='team'
                     AND (c.title LIKE ? OR c.summary LIKE ? OR c.tags LIKE ?)
                   ORDER BY c.id DESC LIMIT 100""",
                (like, like, like),
            )
        else:
            rows = conn.execute(
                """SELECT c.*,u.display_name,u.username,p.avatar_symbol
                   FROM hub_resource_cards c
                   JOIN accounts u ON u.id=c.user_id
                   JOIN account_profiles p ON p.account_id=u.id
                   WHERE u.active=1 AND c.visibility='team'
                   ORDER BY c.id DESC LIMIT 100"""
            )
        cards = [dict(row) for row in rows]
    for card in cards:
        card["tag_list"] = [x.strip() for x in re.split(r"[,，;；\n]", card.get("tags", "")) if x.strip()][:8]
    return templates.TemplateResponse(
        request=request, name="resources.html",
        context=hub_context(request, "resources", cards=cards, query=query),
    )

@router.get("/releases", response_class=HTMLResponse, name="hub_releases")
def releases_page(request: Request):
    require_user(request)
    with connect_hub() as conn:
        releases=[dict(row) for row in conn.execute("SELECT * FROM hub_releases WHERE active=1 ORDER BY id DESC")]
    return templates.TemplateResponse(request=request,name="releases.html",context=hub_context(request,"releases",releases=releases))


@router.get("/releases/{release_id}/download", name="hub_release_download")
def release_download(request: Request, release_id: int, token: str = ""):
    """版本包下载。

    主路径靠登录态 Cookie；桌面内嵌窗口、或把链接发给别人时，Cookie 可能带不上，
    所以额外支持 ``?token=<我的API Token>``。两条路径都必须通过身份校验。
    """
    if current_user(request) is None and not _token_identity(token):
        raise HTTPException(
            status_code=401,
            detail="请先登录同行会，或在下载链接后加上 ?token=你的 API Token。",
        )
    with connect_hub() as conn:
        row = conn.execute("SELECT * FROM hub_releases WHERE id=? AND active=1", (release_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="版本包不存在或已下架。")
    if resolve_release_file(row) is None:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=hub_context(
                request,
                "error",
                status_code=404,
                title="版本包文件不存在",
                message=MISSING_MESSAGE,
            ),
            status_code=404,
        )
    return release_download_response(row)


def _token_identity(token: str):
    from hub.db import now_iso

    value = token.strip()
    if not value:
        return None
    with connect_hub() as conn:
        row = get_user_by_token(conn, value)
        if row:
            conn.execute(
                "UPDATE accounts SET last_seen_at=?,updated_at=? WHERE id=?",
                (now_iso(), now_iso(), row["id"]),
            )
            conn.commit()
        return row
