from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import accounts as accounts_repo
from hub.invites import consume_invite, find_invite, normalize_code
from hub.common import (
    HUB_SESSION_ACCOUNT_LIMIT,
    add_session_account,
    audit,
    can_add_new_session_account,
    can_add_session_account,
    client_ip,
    current_user,
    flash,
    hub_context,
    login_allowed,
    record_login_failure,
    record_login_success,
    remove_session_account,
    session_accounts,
    switch_session_account,
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


@router.get("/login", response_class=HTMLResponse, name="hub_login_page")
def login_page(request: Request):
    user = current_user(request)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=hub_context(
            request,
            "login",
            add_account_mode=bool(user),
        ),
    )


@router.post("/login", name="hub_login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_value: str = Form("", alias="_csrf")):
    verify_csrf(request, csrf_value)
    ip = client_ip(request)
    if not login_allowed(ip):
        flash(request, "登录尝试过多，请10分钟后重试。", "error")
        response = RedirectResponse("/", status_code=303)
        response.headers["Retry-After"] = "600"
        return response
    previous = current_user(request)
    with connect_hub() as conn:
        row = accounts_repo.authenticate(conn, username, password)
        if not row:
            record_login_failure(ip)
            audit(conn, None, "login_failed", username, request)
            conn.commit()
            flash(request, "用户名或密码不正确。", "error")
            return RedirectResponse("/", status_code=303)
        account_id = int(row["id"])
        if not can_add_session_account(request, account_id):
            flash(request, f"同一会话最多保留{HUB_SESSION_ACCOUNT_LIMIT}个账号，请先在右上角退出一个账号。", "error")
            return RedirectResponse("/", status_code=303)
        record_login_success(ip)
        accounts_repo.mark_login(conn, account_id)
        audit(conn, account_id, "login", "网页登录", request)
        conn.commit()
    add_session_account(request, account_id, activate=True)
    display_name = str(row["display_name"])
    if previous:
        if int(previous["id"]) == account_id:
            flash(request, f"已重新进入账号 {display_name}。")
        else:
            flash(request, f"已添加并切换到账号 {display_name}；原账号仍保留在右上角切换列表。")
    else:
        flash(request, f"登录成功，当前账号 {display_name}。")
    return RedirectResponse("/", status_code=303)


@router.get("/register", response_class=HTMLResponse, name="hub_register")
def register_page(
    request: Request,
    invite: str = "",
    u: str = "",
    d: str = "",
    e: str = "",
):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context=hub_context(
            request,
            "register",
            invite=normalize_code(invite),
            username_draft=u[:32],
            display_draft=d[:40],
            email_draft=e[:254],
        ),
    )


def _account_creation_error_message(
    exc: Exception, username: str, email: str | None
) -> str:
    """Translate database failures without mislabelling every error as a duplicate."""
    detail = str(exc).lower()
    if isinstance(exc, sqlite3.IntegrityError):
        if "accounts.username" in detail:
            return f"创建账户失败：用户名 {username} 已被使用，邀请码未扣除，请换一个用户名。"
        if "accounts.email" in detail and email:
            return f"创建账户失败：邮箱 {email} 已被使用，邀请码未扣除，请换一个邮箱或留空。"
        if "foreign key constraint failed" in detail:
            return (
                "创建账户失败：中心数据库仍有关联结构未升级，本次没有创建账号，"
                "邀请码也未扣除。请管理员安装包含中心库迁移的新版后重试。"
            )
        if "unique constraint failed" in detail:
            return "创建账户失败：账号唯一标识冲突，邀请码未扣除，请重试。"
        return "创建账户失败：账号数据未通过完整性校验，邀请码未扣除，请稍后重试。"
    return "创建账户失败：中心数据库暂时不可用，邀请码未扣除，请稍后重试。"


def _retry_url(invite: str, username: str, display_name: str, email: str) -> str:
    parts = []
    code = normalize_code(invite)
    if code:
        parts.append(f"invite={quote(code)}")
    if username.strip():
        parts.append(f"u={quote(username.strip()[:32])}")
    if display_name.strip():
        parts.append(f"d={quote(display_name.strip()[:40])}")
    if email.strip():
        parts.append(f"e={quote(email.strip()[:254])}")
    return "/register" + (("?" + "&".join(parts)) if parts else "")


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
        return RedirectResponse(_retry_url(invite, username, display_name, email), status_code=303)
    try:
        uname = accounts_repo.normalize_username(username)
        clean_display = accounts_repo.normalize_display_name(display_name, uname)
        accounts_repo.validate_password(password)
        clean_email = accounts_repo.normalize_email(email)
    except accounts_repo.AccountError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(_retry_url(invite, username, display_name, email), status_code=303)
    previous = current_user(request)
    if previous and not can_add_new_session_account(request):
        flash(request, f"同一会话最多保留{HUB_SESSION_ACCOUNT_LIMIT}个账号，请先退出一个账号后再创建。", "error")
        return RedirectResponse(_retry_url(invite, username, display_name, email), status_code=303)
    with hub_transaction(immediate=True) as conn:
        max_members = int(get_hub_setting(conn, "max_members", "10"))
        if conn.execute("SELECT COUNT(*) n FROM accounts WHERE active=1").fetchone()["n"] >= max_members:
            flash(
                request,
                f"同行会成员已达到当前上限（{max_members}）。"
                "请管理员在“管理控制台 → 邀请码”调整成员上限后重试。",
                "error",
            )
            return RedirectResponse(_retry_url(invite, username, display_name, email), status_code=303)
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
            return RedirectResponse(_retry_url(invite, username, display_name, email), status_code=303)
        # 建号冲突先于扣次检测：避免先消耗邀请码再回滚，也避免把邮箱冲突误报成用户名冲突。
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
            return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
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
                return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
        if not consume_invite(conn, row["code"]):
            flash(request, "邀请码已用完。", "error")
            return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
        try:
            user_id, _ = create_user(conn, uname, password, clean_display, email=clean_email)
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            flash(
                request,
                _account_creation_error_message(exc, uname, clean_email),
                "error",
            )
            return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
        except ValueError as exc:
            conn.rollback()
            flash(request, str(exc), "error")
            return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
        except sqlite3.Error as exc:
            conn.rollback()
            flash(request, _account_creation_error_message(exc, uname, clean_email), "error")
            return RedirectResponse(_retry_url(row["code"], username, display_name, email), status_code=303)
        audit(conn, user_id, "register", "邀请码注册", request)
    if previous:
        add_session_account(request, user_id, activate=False)
        flash(
            request,
            f"账号 {uname} 已创建并加入右上角账号切换列表；当前仍以 {previous['display_name']} 登录。",
        )
        return RedirectResponse("/", status_code=303)
    add_session_account(request, user_id, activate=True)
    flash(request, f"账号 {uname} 已创建并自动登录。")
    return RedirectResponse("/me", status_code=303)


@router.post("/session/switch", name="hub_session_switch")
def switch_account(
    request: Request,
    account_id: int = Form(...),
    csrf_value: str = Form(..., alias="_csrf"),
):
    verify_csrf(request, csrf_value)
    if not switch_session_account(request, account_id):
        flash(request, "该账号不在当前会话的切换列表中，请重新登录添加。", "error")
        return RedirectResponse("/", status_code=303)
    user = current_user(request)
    flash(request, f"已切换到账号 {user['display_name'] if user else account_id}。")
    return RedirectResponse("/", status_code=303)


@router.post("/logout", name="hub_logout")
def logout(request: Request, csrf_value: str = Form(..., alias="_csrf")):
    verify_csrf(request, csrf_value)
    current = current_user(request)
    accounts = session_accounts(request)
    if current:
        _, next_id = remove_session_account(request, int(current["id"]))
        remaining = [item for item in accounts if int(item["id"]) != int(current["id"])]
        if next_id:
            next_user = next((item for item in remaining if int(item["id"]) == next_id), None)
            flash(
                request,
                f"已退出账号 {current['display_name']}；"
                f"当前切换到 {next_user['display_name'] if next_user else next_id}。",
            )
            return RedirectResponse("/", status_code=303)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.post("/logout/all", name="hub_logout_all")
def logout_all(request: Request, csrf_value: str = Form(..., alias="_csrf")):
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
