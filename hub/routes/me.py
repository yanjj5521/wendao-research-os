from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core import accounts as accounts_repo
from hub.common import (
    audit,
    flash,
    hub_context,
    hub_url_for,
    parse_theme,
    profile_payload,
    require_user,
    templates,
    verify_csrf,
)
from services import hub_storage
from hub.db import (
    HUB_ADMIN_PATH,
    HUB_BACKUP_DIR,
    connect_hub,
    hub_transaction,
    now_iso,
)

router = APIRouter()


@router.get("/me", response_class=HTMLResponse, name="hub_me")
def me_page(request: Request):
    user = require_user(request)
    with connect_hub() as conn:
        payload = profile_payload(conn, int(user["id"]))
        row = accounts_repo.credentials_for(conn, int(user["id"]))
        token = row["api_token"] if row else ""
        cards = [dict(row) for row in conn.execute("SELECT * FROM hub_resource_cards WHERE user_id=? ORDER BY id DESC", (user["id"],))]
    return templates.TemplateResponse(request=request, name="me.html", context=hub_context(request, "me", data=payload, api_token=token, cards=cards))


@router.post("/me/profile", name="hub_profile_save")
def profile_save(
    request: Request,
    display_name: str = Form(...), title: str = Form(""), bio: str = Form(""), skills: str = Form(""),
    capabilities: str = Form(""), goals: str = Form(""), avatar_symbol: str = Form("道"), public: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        conn.execute("UPDATE accounts SET display_name=?,updated_at=? WHERE id=?", (display_name.strip()[:40] or user["username"], now_iso(), user["id"]))
        conn.execute(
            """UPDATE account_profiles SET title=?,bio=?,skills=?,capabilities=?,goals=?,avatar_symbol=?,public=?,revision=revision+1,updated_at=? WHERE user_id=?""",
            (title[:100],bio[:1000],skills[:2000],capabilities[:3000],goals[:1500],avatar_symbol[:2] or "道",int(public=="1"),now_iso(),user["id"]),
        )
        audit(conn, int(user["id"]), "profile_update", "网页更新个人主页", request)
    flash(request, "个人主页已保存，并可被本地节点同步。")
    return RedirectResponse("/me", status_code=303)


@router.post("/me/theme", name="hub_theme_save")
def theme_save(request: Request, accent: str = Form("terracotta"), density: str = Form("comfortable"), scene: str = Form("warm"), home_motto: str = Form(""), home_poem: str = Form(""), csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        row = conn.execute("SELECT theme_json FROM account_profiles WHERE account_id=?", (user["id"],)).fetchone()
        try:
            existing = json.loads(row["theme_json"] or "{}") if row else {}
        except json.JSONDecodeError:
            existing = {}
        existing.update({"accent":accent,"density":density,"scene":scene,"home_motto":home_motto,"home_poem":home_poem})
        theme = parse_theme(existing)
        conn.execute("UPDATE account_profiles SET theme_json=?,revision=revision+1,updated_at=? WHERE user_id=?", (json.dumps(theme,ensure_ascii=False),now_iso(),user["id"]))
        audit(conn, int(user["id"]), "theme_update", json.dumps(theme,ensure_ascii=False), request)
    flash(request, "个性化配置已保存。")
    return RedirectResponse("/me", status_code=303)


@router.get("/me/export", name="hub_me_export")
def me_export(request: Request):
    user = require_user(request)
    with connect_hub() as conn:
        data = profile_payload(conn, int(user["id"]))
        data["resource_cards"] = [dict(row) for row in conn.execute("SELECT title,summary,tags,source_url,visibility,created_at,updated_at FROM hub_resource_cards WHERE user_id=?", (user["id"],))]
    path = HUB_BACKUP_DIR / f"user_{user['username']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return FileResponse(path, filename=path.name, media_type="application/json")


@router.post("/me/avatar", name="hub_avatar_upload")
def avatar_upload(request: Request, file: UploadFile = File(...), csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        data = file.file.read()
        hub_storage.save_avatar(int(user["id"]), data, file.filename or "")
        with hub_transaction() as conn:
            audit(conn, int(user["id"]), "avatar_upload", "更新了联机头像", request)
        flash(request, "头像已更新。")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(hub_url_for(request, "hub_me"), status_code=303)


@router.post("/me/avatar/remove", name="hub_avatar_remove")
def avatar_remove(request: Request, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    hub_storage.remove_avatar(int(user["id"]))
    with hub_transaction() as conn:
        audit(conn, int(user["id"]), "avatar_remove", "移除了联机头像", request)
    flash(request, "头像已移除，恢复为字符头像。")
    return RedirectResponse(hub_url_for(request, "hub_me"), status_code=303)


@router.post("/me/token/regenerate", name="hub_token_regenerate")
def token_regenerate(request: Request, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        accounts_repo.rotate_token(conn, int(user["id"]))
        audit(conn, int(user["id"]), "token_regenerate", "重新生成API Token", request)
    flash(request, "API Token已重新生成，旧Token立即失效。")
    return RedirectResponse("/me", status_code=303)


@router.post("/me/password", name="hub_password_change")
def password_change(request: Request, current_password: str = Form(...), new_password: str = Form(...), new_password_confirm: str = Form(...), csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    if new_password != new_password_confirm:
        flash(request, "两次新密码不一致。", "error")
        return RedirectResponse("/me", status_code=303)
    with hub_transaction() as conn:
        credential = accounts_repo.credentials_for(conn, int(user["id"]))
        if not credential or not accounts_repo.verify_password(
            current_password,
            credential["password_salt"],
            credential["password_hash"],
            int(credential["password_iterations"] or accounts_repo.DEFAULT_ITERATIONS),
        ):
            flash(request, "当前密码不正确。", "error")
            return RedirectResponse("/me", status_code=303)
        try:
            accounts_repo.set_password(conn, int(user["id"]), new_password)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/me", status_code=303)
        audit(conn, int(user["id"]), "password_change", "修改密码", request)
    credentials_retired = False
    if user["role"] == "admin" and HUB_ADMIN_PATH.exists():
        HUB_ADMIN_PATH.unlink(missing_ok=True)
        credentials_retired = True
    flash(
        request,
        "密码已修改；首次一次性凭据文件已销毁，API Token 仍可在本页查看。"
        if credentials_retired
        else "密码已修改。",
    )
    return RedirectResponse("/me", status_code=303)


@router.post("/me/cards", name="hub_card_create")
def card_create(request: Request, title: str = Form(...), summary: str = Form(""), tags: str = Form(""), source_url: str = Form(""), csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    if source_url and not re.match(r"^https?://", source_url.strip(), re.I):
        flash(request, "来源链接必须以http://或https://开头。", "error")
        return RedirectResponse("/me", status_code=303)
    with hub_transaction() as conn:
        ts=now_iso()
        conn.execute("INSERT INTO hub_resource_cards(user_id,title,summary,tags,source_url,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (user["id"],title[:160],summary[:1000],tags[:300],source_url[:500],ts,ts))
        audit(conn,int(user["id"]),"resource_card_create",title,request)
    flash(request,"共享资料卡已发布；原文件仍留在你自己的电脑。")
    return RedirectResponse("/me",status_code=303)


@router.post("/me/cards/{card_id}/delete", name="hub_card_delete")
def card_delete(request: Request, card_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    with hub_transaction() as conn:
        conn.execute("DELETE FROM hub_resource_cards WHERE id=? AND user_id=?",(card_id,user["id"]))
    flash(request,"资料卡已删除。")
    return RedirectResponse("/me",status_code=303)
