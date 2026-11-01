from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import accounts as accounts_repo
from hub.invites import consume_invite, find_invite, normalize_code
from hub.common import (
    audit,
    client_ip,
    flash,
    hub_context,
    login_allowed,
    record_login_failure,
    record_login_success,
    templates,
    verify_csrf,
)
from hub.db import (
    ensure_instance_id,
    connect_hub,
    create_user,
    get_hub_setting,
    hub_transaction,
)

router = APIRouter()


@router.post("/login", name="hub_login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_value: str = Form("", alias="_csrf")):
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
        request.session.clear()
        request.session["hub_user_id"] = int(row["id"])
        record_login_success(ip)
        accounts_repo.mark_login(conn, int(row["id"]))
        audit(conn, int(row["id"]), "login", "网页登录", request)
        conn.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse, name="hub_register")
def register_page(request: Request, invite: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context=hub_context(request, "register", invite=normalize_code(invite)),
    )


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
    with hub_transaction(immediate=True) as conn:
        max_members = int(get_hub_setting(conn, "max_members", "10"))
        if conn.execute("SELECT COUNT(*) n FROM accounts WHERE active=1").fetchone()["n"] >= max_members:
            flash(request, "同行会成员已达到当前上限。", "error")
            return RedirectResponse("/register", status_code=303)
        row, reason = find_invite(conn, invite)
        if not row:
            instance = ensure_instance_id(conn)
            messages = {
                "empty": "请输入邀请码。",
                "format": "邀请码格式不正确，请粘贴管理员生成的完整邀请码或注册链接。",
                "missing": (
                    f"邀请码不存在或已失效（中心实例 {instance}）。"
                    "请确认注册地址与管理员面板属于同一实例，或让管理员重新生成。"
                ),
                "used": "邀请码已用完。",
                "expired": "邀请码已过期。",
            }
            flash(request, messages.get(reason, "邀请码无效。"), "error")
            preserved = normalize_code(invite)
            target = (
                "/register"
                if reason in {"empty", "format"} or not preserved
                else f"/register?invite={quote(preserved)}"
            )
            return RedirectResponse(target, status_code=303)
        # 建号冲突先于扣次检测：避免先消耗邀请码再回滚，也避免把邮箱冲突误报成用户名冲突。
        uname = username.strip().lower()
        existing = conn.execute(
            "SELECT username,status FROM accounts WHERE username=?", (uname,)
        ).fetchone()
        if existing:
            if uname == "admin":
                flash(
                    request,
                    "admin 是首次启动时自动创建的管理员账号，请直接登录；"
                    "忘记密码时使用 WendaoResearchV3.exe admin-path 查看凭据文件，无需重新注册。",
                    "error",
                )
            elif existing["status"] != "active":
                flash(
                    request,
                    f"用户名 {existing['username']} 已被停用账号占用，请联系管理员恢复或换一个。",
                    "error",
                )
            else:
                flash(request, f"用户名 {existing['username']} 已被使用，请换一个。", "error")
            return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        clean_email = email.strip()
        if clean_email:
            taken = conn.execute(
                "SELECT username FROM accounts WHERE email=? COLLATE NOCASE", (clean_email,)
            ).fetchone()
            if taken:
                flash(
                    request,
                    f"邮箱 {clean_email} 已被账号 {taken['username']} 使用，请换一个邮箱或留空。",
                    "error",
                )
                return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        if not consume_invite(conn, row["code"]):
            flash(request, "邀请码已用完。", "error")
            return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        try:
            user_id, _ = create_user(conn, username, password, display_name, email=email.strip() or None)
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(request, "创建账户失败：账号信息冲突，请更换用户名或邮箱后重试。", "error")
            return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        except ValueError as exc:
            conn.rollback()
            flash(request, str(exc), "error")
            return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        except sqlite3.Error:
            conn.rollback()
            flash(request, "创建账户失败，请稍后重试。", "error")
            return RedirectResponse(f"/register?invite={quote(row['code'])}", status_code=303)
        audit(conn, user_id, "register", "邀请码注册", request)
        request.session.clear()
        request.session["hub_user_id"] = user_id
    return RedirectResponse("/me", status_code=303)


@router.post("/logout", name="hub_logout")
def logout(request: Request, csrf_value: str = Form(..., alias="_csrf")):
    verify_csrf(request, csrf_value)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.post("/session/reset", name="hub_session_reset")
def session_reset(request: Request):
    """Break out of an expired/foreign session without needing a CSRF token.

    A stale cookie can make every POST (including logout) fail CSRF, leaving the
    user stuck.  Clearing the session is idempotent and has no side effects, so
    this escape hatch deliberately skips CSRF.
    """
    request.session.clear()
    return RedirectResponse("/", status_code=303)
