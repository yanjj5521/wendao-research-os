from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import accounts as accounts_repo
from hub.common import (
    audit,
    client_ip,
    flash,
    hub_context,
    login_allowed,
    record_login_failure,
    templates,
    verify_csrf,
)
from hub.db import (
    connect_hub,
    create_user,
    get_hub_setting,
    hub_transaction,
)

router = APIRouter()


@router.post("/login", name="hub_login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_value: str = Form(..., alias="_csrf")):
    verify_csrf(request, csrf_value)
    ip = client_ip(request)
    if not login_allowed(ip):
        flash(request, "登录尝试过多，请10分钟后再试。", "error")
        response = RedirectResponse("/", status_code=303)
        response.headers["Retry-After"] = "600"
        return response
    with connect_hub() as conn:
        row = accounts_repo.authenticate(conn, username, password)
        if not row:
            record_login_failure(ip)
            audit(conn, int(row["id"]) if row else None, "login_failed", username, request)
            conn.commit()
            flash(request, "用户名或密码不正确。", "error")
            return RedirectResponse("/", status_code=303)
        request.session["hub_user_id"] = int(row["id"])
        accounts_repo.mark_login(conn, int(row["id"]))
        audit(conn, int(row["id"]), "login", "网页登录", request)
        conn.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse, name="hub_register")
def register_page(request: Request, invite: str = ""):
    return templates.TemplateResponse(request=request, name="register.html", context=hub_context(request, "register", invite=invite))


@router.post("/register", name="hub_register_submit")
def register_submit(
    request: Request,
    invite: str = Form(...),
    username: str = Form(...),
    display_name: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_value: str = Form(..., alias="_csrf"),
):
    verify_csrf(request, csrf_value)
    if password != password_confirm:
        flash(request, "两次密码不一致。", "error")
        return RedirectResponse(f"/register?invite={invite}", status_code=303)
    with hub_transaction() as conn:
        max_members = int(get_hub_setting(conn, "max_members", "10"))
        if conn.execute("SELECT COUNT(*) n FROM accounts WHERE active=1").fetchone()["n"] >= max_members:
            flash(request, "同行会成员已达到当前上限。", "error")
            return RedirectResponse("/register", status_code=303)
        row = conn.execute("SELECT * FROM hub_invites WHERE code=?", (invite.strip(),)).fetchone()
        if not row or int(row["uses_remaining"]) <= 0:
            flash(request, "邀请码无效或已用完。", "error")
            return RedirectResponse("/register", status_code=303)
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now().astimezone():
            flash(request, "邀请码已过期。", "error")
            return RedirectResponse("/register", status_code=303)
        try:
            user_id, _ = create_user(conn, username, password, display_name, email=email.strip() or None)
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(request, "用户名已经存在。", "error")
            return RedirectResponse(f"/register?invite={invite}", status_code=303)
        except ValueError as exc:
            conn.rollback()
            flash(request, str(exc), "error")
            return RedirectResponse(f"/register?invite={invite}", status_code=303)
        except sqlite3.Error:
            conn.rollback()
            flash(request, "创建账户失败，请稍后重试。", "error")
            return RedirectResponse(f"/register?invite={invite}", status_code=303)
        conn.execute("UPDATE hub_invites SET uses_remaining=uses_remaining-1 WHERE code=?", (invite.strip(),))
        audit(conn, user_id, "register", "邀请码注册", request)
        request.session["hub_user_id"] = user_id
    return RedirectResponse("/me", status_code=303)


@router.post("/logout", name="hub_logout")
def logout(request: Request, csrf_value: str = Form(..., alias="_csrf")):
    verify_csrf(request, csrf_value)
    request.session.clear()
    return RedirectResponse("/", status_code=303)
