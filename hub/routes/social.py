from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hub.common import (
    audit,
    flash,
    hub_context,
    require_user,
    templates,
    verify_csrf,
)
from hub.social import (
    SOCIAL_ASSET_LABELS,
    social_text,
)
from hub.db import (
    connect_hub,
    hub_transaction,
    now_iso,
)

router = APIRouter()


@router.get("/forum", response_class=HTMLResponse, name="hub_forum")
def forum_page(request: Request):
    require_user(request)
    with connect_hub() as conn:
        posts = [
            dict(row)
            for row in conn.execute(
                """SELECT p.*,u.display_name,u.username,h.avatar_symbol,
                          COUNT(r.id) AS reply_count
                   FROM hub_forum_posts p
                   JOIN accounts u ON u.id=p.user_id
                   JOIN account_profiles h ON h.account_id=p.user_id
                   LEFT JOIN hub_forum_replies r ON r.post_id=p.id
                   WHERE u.active=1
                   GROUP BY p.id ORDER BY p.updated_at DESC,p.id DESC LIMIT 40"""
            )
        ]
        replies = [
            dict(row)
            for row in conn.execute(
                """SELECT r.*,u.display_name,h.avatar_symbol FROM hub_forum_replies r
                   JOIN accounts u ON u.id=r.user_id
                   JOIN account_profiles h ON h.account_id=r.user_id
                   WHERE u.active=1 ORDER BY r.created_at ASC"""
            )
        ]
    replies_by_post: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for reply in replies:
        replies_by_post[int(reply["post_id"])].append(reply)
    return templates.TemplateResponse(
        request=request,
        name="forum.html",
        context=hub_context(request, "forum", posts=posts, replies_by_post=replies_by_post, social_asset_labels=SOCIAL_ASSET_LABELS),
    )


@router.post("/forum", name="hub_forum_create")
def forum_create(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    tags: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        clean_title = social_text(title, limit=100, label="标题")
        clean_body = social_text(body, limit=2400, label="内容")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/forum", status_code=303)
    with hub_transaction() as conn:
        ts = now_iso()
        conn.execute(
            "INSERT INTO hub_forum_posts(user_id,title,body,tags,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (user["id"], clean_title, clean_body, tags.strip()[:160], ts, ts),
        )
        audit(conn, int(user["id"]), "forum_post", clean_title, request)
    flash(request, "帖子已发布。只写你愿意公开给同行看的问题、方法或线索。")
    return RedirectResponse("/forum", status_code=303)


@router.post("/forum/{post_id}/reply", name="hub_forum_reply")
def forum_reply(
    request: Request,
    post_id: int,
    body: str = Form(...),
    csrf_value: str = Form(..., alias="_csrf"),
):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        clean_body = social_text(body, limit=1200, label="回复")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/forum", status_code=303)
    with hub_transaction() as conn:
        post = conn.execute("SELECT user_id FROM hub_forum_posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            flash(request, "帖子已不存在。", "error")
            return RedirectResponse("/forum", status_code=303)
        ts = now_iso()
        conn.execute(
            "INSERT INTO hub_forum_replies(post_id,user_id,body,created_at) VALUES (?,?,?,?)",
            (post_id, user["id"], clean_body, ts),
        )
        conn.execute("UPDATE hub_forum_posts SET updated_at=? WHERE id=?", (ts, post_id))
        audit(conn, int(user["id"]), "forum_reply", f"post:{post_id}", request)
    return RedirectResponse("/forum", status_code=303)


@router.post("/forum/{post_id}/delete", name="hub_forum_post_delete")
def forum_post_delete(request: Request, post_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        post = conn.execute("SELECT user_id,title FROM hub_forum_posts WHERE id=?", (post_id,)).fetchone()
        if post and (int(post["user_id"]) == int(user["id"]) or user["role"] == "admin"):
            conn.execute("DELETE FROM hub_forum_posts WHERE id=?", (post_id,))
            audit(conn, int(user["id"]), "forum_post_delete", str(post["title"]), request)
            flash(request, "帖子已删除。")
        else:
            flash(request, "只能删除自己的帖子。", "error")
    return RedirectResponse("/forum", status_code=303)


@router.post("/forum/replies/{reply_id}/delete", name="hub_forum_reply_delete")
def forum_reply_delete(request: Request, reply_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        reply = conn.execute("SELECT user_id FROM hub_forum_replies WHERE id=?", (reply_id,)).fetchone()
        if reply and (int(reply["user_id"]) == int(user["id"]) or user["role"] == "admin"):
            conn.execute("DELETE FROM hub_forum_replies WHERE id=?", (reply_id,))
            audit(conn, int(user["id"]), "forum_reply_delete", f"reply:{reply_id}", request)
        else:
            flash(request, "只能删除自己的回复。", "error")
    return RedirectResponse("/forum", status_code=303)


@router.get("/chat", response_class=HTMLResponse, name="hub_chat")
def chat_page(request: Request):
    require_user(request)
    with connect_hub() as conn:
        messages = [
            dict(row)
            for row in conn.execute(
                """SELECT c.*,u.display_name,h.avatar_symbol FROM hub_chat_messages c
                   JOIN accounts u ON u.id=c.user_id
                   JOIN account_profiles h ON h.account_id=c.user_id
                   WHERE u.active=1 ORDER BY c.id DESC LIMIT 80"""
            )
        ]
    messages.reverse()
    return templates.TemplateResponse(request=request, name="chat.html", context=hub_context(request, "chat", messages=messages, chat_refresh_seconds=20))


@router.post("/chat", name="hub_chat_send")
def chat_send(request: Request, body: str = Form(...), csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        clean_body = social_text(body, limit=500, label="消息")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/chat", status_code=303)
    with hub_transaction() as conn:
        conn.execute("INSERT INTO hub_chat_messages(user_id,body,created_at) VALUES (?,?,?)", (user["id"], clean_body, now_iso()))
        audit(conn, int(user["id"]), "chat_send", clean_body[:80], request)
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/{message_id}/delete", name="hub_chat_delete")
def chat_delete(request: Request, message_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        message = conn.execute("SELECT user_id FROM hub_chat_messages WHERE id=?", (message_id,)).fetchone()
        if message and (int(message["user_id"]) == int(user["id"]) or user["role"] == "admin"):
            conn.execute("DELETE FROM hub_chat_messages WHERE id=?", (message_id,))
            audit(conn, int(user["id"]), "chat_delete", f"message:{message_id}", request)
        else:
            flash(request, "只能删除自己的消息。", "error")
    return RedirectResponse("/chat", status_code=303)
