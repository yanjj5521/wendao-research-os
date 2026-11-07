from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from services import hub_storage
from services.network_access import member_access_urls, normalize_base_url
from hub.common import (
    hub_url_for,
    audit,
    flash,
    hub_context,
    require_user,
    templates,
    verify_csrf,
)
from hub.invites import generate_invite_code, normalize_code
from hub.db import (
    ASSET_KEYS,
    get_hub_setting,
    HUB_ADMIN_PATH,
    HUB_BACKUP_DIR,
    HUB_RELEASE_DIR,
    WELFARE_SINGLE_LIMITS,
    hub_instance_id,
    set_hub_setting,
    backup_hub_db,
    connect_hub,
    enforce_welfare_weekly_limits,
    display_realm,
    weekly_quotas,
    weekly_quota_meta,
    weekly_quota_key,
    WELFARE_LABELS,
    hub_transaction,
    normalize_welfare_values,
    now_iso,
    transact_asset,
)

"""同行会管理端：成员、邀请、版本、福利、审计与数据导出。

权限矩阵（require_user(admin=True) 为硬门禁，全部动作入审计）：
- 成员（member）：论坛/聊天/坊市/资料卡/自己的主页与头像；只读公开成员名册。
- 管理员（admin）：邀请码生成、账号启停、头像重置、福利发放、版本发布、
  数据库备份、账号留存导出（CSV）、审计日志查看。
- 两个角色的边界由路由层强制；成员无法访问任何 /admin/* 与导出端点。
"""

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse, name="hub_admin")
def admin_page(request: Request):
    require_user(request,admin=True)
    with connect_hub() as conn:
        users=[
            {**dict(row), "realm": display_realm(row["metadata_json"], row["cultivation"])}
            for row in conn.execute(
                """
                SELECT a.id,a.username,a.display_name,a.email,a.role,a.status,a.active,
                       a.created_at,a.last_login_at,a.last_seen_at,a.metadata_json,
                       (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                         WHERE t.user_id=a.id AND t.asset_key='spirit_stone') AS spirit_stone,
                       (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                         WHERE t.user_id=a.id AND t.asset_key='cultivation') AS cultivation
                FROM accounts a ORDER BY a.role='admin' DESC, a.id
                """
            )
        ]
        invites=[dict(row) for row in conn.execute("SELECT * FROM hub_invites ORDER BY rowid DESC")]
        releases=[dict(row) for row in conn.execute("SELECT * FROM hub_releases ORDER BY id DESC")]
        audits=[dict(row) for row in conn.execute("SELECT a.*,u.display_name FROM hub_audit_log a LEFT JOIN accounts u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 50")]
        welfare=[dict(row) for row in conn.execute("""SELECT g.*,r.display_name recipient_name,a.display_name admin_name
            FROM hub_welfare_grants g JOIN accounts r ON r.id=g.recipient_id JOIN accounts a ON a.id=g.granted_by
            ORDER BY g.id DESC LIMIT 12""")]
        raw_public_base_url = get_hub_setting(conn, "public_base_url", "") or os.environ.get("HUB_PUBLIC_URL", "").strip()
        public_base_url, public_base_error = normalize_base_url(raw_public_base_url)
        runtime_base_url = get_hub_setting(conn, "runtime_base_url", "")
    request_base_url = str(request.base_url).rstrip("/")
    network_addresses = []
    if runtime_base_url:
        try:
            runtime_parts = urlsplit(runtime_base_url)
            runtime_port = runtime_parts.port or (443 if runtime_parts.scheme == "https" else 80)
        except ValueError:
            runtime_port = 0
        if runtime_port:
            network_addresses = member_access_urls(runtime_port)
    discovered_base_url = str(network_addresses[0]["url"]) if network_addresses else ""
    register_base_url = (
        public_base_url or discovered_base_url or runtime_base_url or request_base_url
    ).rstrip("/")
    for invite in invites:
        invite["register_url"] = (
            f"{register_base_url}/register?invite={quote(str(invite['code']))}"
        )
    try:
        register_host = (urlsplit(register_base_url).hostname or "").casefold()
    except ValueError:
        register_host = ""
    register_base_is_local = register_host in {"localhost", "127.0.0.1", "::1"}
    today = now_iso()[:10]
    week_ago = (datetime.now().astimezone() - timedelta(days=7)).isoformat(timespec="seconds")
    month_ago = (datetime.now().astimezone() - timedelta(days=30)).isoformat(timespec="seconds")
    stats = {
        "members_total": int(conn.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"]),
        "members_active": int(conn.execute("SELECT COUNT(*) n FROM accounts WHERE status='active'").fetchone()["n"]),
        "new_7d": int(conn.execute("SELECT COUNT(*) n FROM accounts WHERE created_at>=?", (week_ago,)).fetchone()["n"]),
        "new_30d": int(conn.execute("SELECT COUNT(*) n FROM accounts WHERE created_at>=?", (month_ago,)).fetchone()["n"]),
        "active_today": int(conn.execute("SELECT COUNT(*) n FROM accounts WHERE substr(last_seen_at,1,10)=?", (today,)).fetchone()["n"]),
        "forum_posts": int(conn.execute("SELECT COUNT(*) n FROM hub_forum_posts").fetchone()["n"]),
        "chat_messages": int(conn.execute("SELECT COUNT(*) n FROM hub_chat_messages").fetchone()["n"]),
        "transfers": int(conn.execute("SELECT COUNT(*) n FROM hub_social_transfers").fetchone()["n"]),
        "audit_total": int(conn.execute("SELECT COUNT(*) n FROM hub_audit_log").fetchone()["n"]),
    }
    backup_files = sorted(HUB_BACKUP_DIR.glob("hub_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=hub_context(
            request,
            "admin",
            users=users,
            invites=invites,
            instance_id=hub_instance_id(conn),
            member_cap=int(get_hub_setting(conn, "max_members", "10")),
            member_used=int(conn.execute("SELECT COUNT(*) n FROM accounts WHERE active=1").fetchone()["n"]),
            releases=releases,
            audits=audits,
            admin_credentials_path=str(HUB_ADMIN_PATH),
            stats=stats,
            backup_count=len(backup_files),
            last_backup=backup_files[0].name if backup_files else "尚无",
            welfare=welfare,
            welfare_limits=WELFARE_SINGLE_LIMITS,
            welfare_labels=WELFARE_LABELS,
            welfare_weekly=weekly_quotas(conn),
            welfare_meta=weekly_quota_meta(conn),
            welfare_week_key=weekly_quota_key(),
            public_base_url=public_base_url,
            public_base_error=public_base_error,
            runtime_base_url=runtime_base_url,
            register_base_url=register_base_url,
            register_base_is_local=register_base_is_local,
            network_addresses=network_addresses,
        ),
    )


def _stored_invite_code(conn, raw_code: str) -> str | None:
    """Resolve normalized admin input to the exact stored invite-code value."""
    code = normalize_code(raw_code)
    if not code:
        return None
    row = conn.execute(
        "SELECT code FROM hub_invites WHERE TRIM(code)=? COLLATE NOCASE "
        "ORDER BY created_at DESC LIMIT 1",
        (code,),
    ).fetchone()
    if row:
        return str(row["code"])
    for candidate in conn.execute("SELECT code FROM hub_invites"):
        stored = str(candidate["code"])
        if normalize_code(stored).casefold() == code.casefold():
            return stored
    return None


@router.post("/admin/network", name="hub_admin_network")
def update_network(
    request: Request,
    public_base_url: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    normalized, error = normalize_base_url(public_base_url)
    if error:
        flash(request, error, "error")
        return RedirectResponse("/admin", status_code=303)
    with hub_transaction(immediate=True) as conn:
        set_hub_setting(conn, "public_base_url", normalized)
        audit(
            conn,
            int(admin["id"]),
            "network_base_update",
            normalized or "自动检测局域网/VPN 地址",
            request,
        )
    flash(
        request,
        "固定成员入口已保存。" if normalized else "已清除固定入口，恢复自动检测。",
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/invites/delete", name="hub_invite_delete")
def invite_delete(
    request: Request,
    code: str = Form(...),
    csrf_value: str = Form(..., alias="_csrf"),
):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        stored = _stored_invite_code(conn, code)
        if not stored:
            flash(request, "邀请码不存在或已被删除。", "error")
            return RedirectResponse("/admin", status_code=303)
        conn.execute("DELETE FROM hub_invites WHERE code=?", (stored,))
        audit(conn, int(admin["id"]), "invite_delete", stored, request)
    flash(request, f"邀请码 {stored} 已删除；已有账号不受影响。")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/invites", name="hub_invite_create")
def invite_create(request:Request,uses:int=Form(1),days:int=Form(14),csrf_value:str=Form(..., alias="_csrf")):
    user = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    code=generate_invite_code()
    expires=(datetime.now().astimezone()+timedelta(days=max(1,min(days,90)))).isoformat(timespec="seconds")
    with hub_transaction() as conn:
        conn.execute("INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,?,?)",(code,max(1,min(uses,10)),expires,user["id"],now_iso()))
        audit(conn,int(user["id"]),"invite_create",code,request)
    flash(request,f"邀请码已生成：{code}")
    return RedirectResponse("/admin",status_code=303)


@router.post("/admin/settings", name="hub_admin_settings")
def admin_settings(request:Request,max_members:int=Form(10),csrf_value:str=Form(..., alias="_csrf")):
    user = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    cap = max(2, min(200, int(max_members)))
    with hub_transaction(immediate=True) as conn:
        set_hub_setting(conn, "max_members", str(cap))
        audit(conn, int(user["id"]), "member_cap_update", f"成员上限调整为 {cap}", request)
    flash(request, f"成员上限已更新为 {cap}。")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/toggle", name="hub_user_toggle")
def user_toggle(request:Request,user_id:int,csrf_value:str=Form(..., alias="_csrf")):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    if int(admin["id"])==user_id:
        flash(request, "不能停用当前管理员。", "error")
        return RedirectResponse("/admin", status_code=303)
    with hub_transaction() as conn:
        target = conn.execute(
            "SELECT status,role FROM accounts WHERE id=?", (user_id,)
        ).fetchone()
        if not target:
            flash(request, "成员不存在。", "error")
            return RedirectResponse("/admin", status_code=303)
        if target["role"] == "admin":
            flash(request, "管理员账号不能通过此按钮停用。", "error")
            return RedirectResponse("/admin", status_code=303)
        next_status = "disabled" if target["status"] == "active" else "active"
        conn.execute(
            "UPDATE accounts SET status=?,updated_at=? WHERE id=?",
            (next_status, now_iso(), user_id),
        )
        audit(conn,int(admin["id"]),"user_toggle",str(user_id),request)
    return RedirectResponse("/admin",status_code=303)


@router.post("/admin/welfare", name="hub_welfare_grant")
def welfare_grant(
    request: Request,
    recipient_id: int = Form(...),
    xp: int = Form(0), cultivation: int = Form(0),
    spirit_stone: int = Form(0), spirit_wood: int = Form(0),
    qi_grass: int = Form(0), dew_flower: int = Form(0), red_ganoderma: int = Form(0),
    azure_sword: int = Form(0), heart_mirror: int = Form(0), cosmos_bag: int = Form(0),
    mystic_iron: int = Form(0), star_sand: int = Form(0), primordial_stone: int = Form(0),
    reason: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    if int(recipient_id) == int(admin["id"]):
        flash(request, "福利只能赠与其他成员，不能给自己。", "error")
        return RedirectResponse("/admin", status_code=303)
    try:
        values = normalize_welfare_values({
            "xp": xp, "cultivation": cultivation,
            "spirit_stone": spirit_stone, "spirit_wood": spirit_wood,
            "qi_grass": qi_grass, "dew_flower": dew_flower, "red_ganoderma": red_ganoderma,
            "azure_sword": azure_sword, "heart_mirror": heart_mirror, "cosmos_bag": cosmos_bag,
            "mystic_iron": mystic_iron, "star_sand": star_sand, "primordial_stone": primordial_stone,
        })
        # immediate：开始即取写锁，"读额度→插入"对并发管理员是原子的，不会超发
        with hub_transaction(immediate=True) as conn:
            member = conn.execute("SELECT id,display_name FROM accounts WHERE id=? AND role='member' AND active=1", (recipient_id,)).fetchone()
            if not member:
                raise ValueError("成员不存在或已停用。")
            enforce_welfare_weekly_limits(conn, int(recipient_id), values)
            grant_columns = ",".join(values)
            grant_marks = ",".join("?" for _ in values)
            cursor = conn.execute(
                f"""INSERT INTO hub_welfare_grants(recipient_id,granted_by,{grant_columns},reason,created_at)
                    VALUES (?,?,{grant_marks},?,?)""",
                (recipient_id, admin["id"], *[values[k] for k in values],
                 reason.strip()[:160] or "同行会福利", now_iso()),
            )
            grant_id = int(cursor.lastrowid)
            for asset in ASSET_KEYS:
                if values[asset]:
                    transact_asset(conn, int(recipient_id), asset, values[asset], f"同行会福利 #{grant_id}", f"welfare:{grant_id}:{asset}")
            audit(conn, int(admin["id"]), "welfare_grant", f"#{grant_id} → {member['display_name']}", request)
    except (ValueError, sqlite3.Error) as exc:
        flash(request, f"福利未发放：{exc}", "error")
        return RedirectResponse("/admin", status_code=303)
    flash(request, "福利已入账；成员下次点击同步后即可领取修为与材料。")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/releases", name="hub_release_upload")
def release_upload(request:Request,version:str=Form(...),title:str=Form(...),notes:str=Form(""),file:UploadFile=File(...),csrf_value:str=Form(..., alias="_csrf")):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        flash(request, "版本包必须为ZIP。", "error")
        return RedirectResponse("/admin", status_code=303)
    safe_version=re.sub(r"[^0-9A-Za-z._-]+","_",version.strip())[:40]
    if not safe_version:
        flash(request, "版本号无效。", "error")
        return RedirectResponse("/admin", status_code=303)
    with connect_hub() as conn:
        duplicated = conn.execute(
            "SELECT 1 FROM hub_releases WHERE version=? AND active=1", (safe_version,)
        ).fetchone()
    if duplicated:
        flash(
            request,
            f"v{safe_version} 已经存在。请先把旧版本下架，或改用新版本号重新发布，"
            "否则成员端会一直拿到旧包。",
            "error",
        )
        return RedirectResponse("/admin", status_code=303)
    try:
        HUB_RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        target=HUB_RELEASE_DIR/f"ResearchCultivationOS_{safe_version}_{secrets.token_hex(4)}.zip"
        temporary=target.with_suffix(".upload")
        h = hashlib.sha256()
        size = 0
        max_size = 500 * 1024 * 1024
        with temporary.open("wb") as out:
            while chunk:=file.file.read(1024*1024):
                size += len(chunk)
                if size > max_size:
                    raise ValueError("版本包超过500MB，请精简后重新发布。")
                h.update(chunk)
                out.write(chunk)
        if size < 1024:
            raise ValueError("版本包为空或过小，请上传正式便携 ZIP。")
        try:
            with zipfile.ZipFile(temporary) as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
                damaged = archive.testzip()
                if damaged:
                    raise ValueError(f"ZIP 校验失败：{damaged} 已损坏。")
                required = {"portable.flag", "使用说明.txt"}
                basenames = {Path(name).name for name in names}
                exe_count = sum(Path(name).suffix.lower() == ".exe" for name in names)
                if exe_count != 1 or not required.issubset(basenames):
                    raise ValueError("请上传构建产生的 Team-Update.zip（必须含唯一 EXE、portable.flag 与使用说明.txt）。")
        except zipfile.BadZipFile as exc:
            raise ValueError("文件不是有效 ZIP，或传输过程中已经损坏。") from exc
        temporary.replace(target)
        with hub_transaction() as conn:
            conn.execute("INSERT INTO hub_releases(version,title,notes,file_path,file_size,sha256,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",(safe_version,title[:160],notes[:4000],str(target.resolve()),size,h.hexdigest(),admin["id"],now_iso()))
            audit(conn,int(admin["id"]),"release_upload",safe_version,request)
    except (OSError, ValueError, sqlite3.Error) as exc:
        if 'temporary' in locals():
            temporary.unlink(missing_ok=True)
        if 'target' in locals():
            target.unlink(missing_ok=True)
        flash(request, f"版本发布失败：{exc}", "error")
        return RedirectResponse("/admin", status_code=303)
    flash(request,f"v{safe_version} 已发布。")
    return RedirectResponse("/admin",status_code=303)


@router.post("/admin/backup", name="hub_backup")
def admin_backup(request:Request,csrf_value:str=Form(..., alias="_csrf")):
    require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    path = backup_hub_db()
    flash(request, f"联机数据库已备份：{path.name}")
    return RedirectResponse("/admin",status_code=303)


@router.get("/admin/export/accounts.csv", name="hub_export_accounts")
def export_accounts(request: Request):
    """账号留存导出：开发者可随时下载全部账号与资产信息（CSV，UTF-8 BOM）。"""
    admin = require_user(request, admin=True)
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "账号", "显示名称", "邮箱", "角色", "状态", "注册时间", "最后登录", "最近在线",
        "灵石", "灵木", "玄铁", "星砂",
    ])
    with connect_hub() as conn:
        rows = conn.execute(
            """
            SELECT a.username,a.display_name,a.email,a.role,a.status,a.created_at,a.last_login_at,a.last_seen_at,
                   (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                     WHERE t.user_id=a.id AND t.asset_key='spirit_stone') spirit_stone,
                   (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                     WHERE t.user_id=a.id AND t.asset_key='spirit_wood') spirit_wood,
                   (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                     WHERE t.user_id=a.id AND t.asset_key='mystic_iron') mystic_iron,
                   (SELECT COALESCE(SUM(amount),0) FROM hub_asset_transactions t
                     WHERE t.user_id=a.id AND t.asset_key='star_sand') star_sand
            FROM accounts a ORDER BY a.id
            """
        ).fetchall()
        for row in rows:
            writer.writerow([row[key] for key in (
                "username", "display_name", "email", "role", "status", "created_at",
                "last_login_at", "last_seen_at", "spirit_stone", "spirit_wood",
                "mystic_iron", "star_sand",
            )])
    with connect_hub() as conn:
        audit(conn, int(admin["id"]), "export_accounts", f"导出 {len(rows)} 条账号记录", request)
    stamp = now_iso()[:10].replace("-", "")
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=hub_accounts_{stamp}.csv"},
    )


@router.post("/admin/users/{user_id}/reset-avatar", name="hub_user_reset_avatar")
def reset_avatar(request: Request, user_id: int, csrf_value: str = Form(..., alias="_csrf")):
    admin = require_user(request, admin=True)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        target = conn.execute("SELECT display_name FROM accounts WHERE id=?", (int(user_id),)).fetchone()
        if not target:
            flash(request, "成员不存在。", "error")
            return RedirectResponse(hub_url_for(request, "hub_admin"), status_code=303)
        hub_storage.remove_avatar(int(user_id))
        audit(conn, int(admin["id"]), "avatar_reset", f"重置了 {target['display_name']} 的联机头像", request)
    flash(request, f"已重置 {target['display_name']} 的联机头像。")
    return RedirectResponse(hub_url_for(request, "hub_admin"), status_code=303)


@router.get("/admin/export/feedback.csv", name="hub_export_feedback")
def export_feedback(request: Request):
    """反馈留存导出（CSV，UTF-8 BOM）。"""
    admin = require_user(request, admin=True)
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["时间", "成员", "类型", "内容", "联系方式"])
    with connect_hub() as conn:
        rows = conn.execute(
            """SELECT f.created_at,f.kind,f.content,f.contact,u.username
               FROM hub_feedback f LEFT JOIN accounts u ON u.id=f.account_id ORDER BY f.id"""
        ).fetchall()
        for row in rows:
            writer.writerow([row["created_at"], row["username"] or "-", row["kind"], row["content"], row["contact"]])
        audit(conn, int(admin["id"]), "export_feedback", f"导出 {len(rows)} 条反馈", request)
    stamp = now_iso()[:10].replace("-", "")
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=hub_feedback_{stamp}.csv"},
    )
