from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core.database import connect, get_setting, now_iso, set_setting
from core.navigation import normalize_nav_labels, normalize_nav_layout
from core.runtime_paths import DATA_ROOT, STORAGE_ROOT
from services.online_sync import (
    best_effort_sync,
    cached_value,
    queue_event,
    retry_paused_events,
    sync_health,
    sync_now,
    test_connection,
)
from services.economy import balances as local_balances
from services.profile_media import export_avatar_payload, import_avatar_payload
from services.progression import normalize_realm_labels
from services.sync_backend import (
    SYNC_CONTRACT_VERSION,
    all_backend_capabilities,
    build_sync_backend,
    validate_hub_url,
)
from services.workspace_profiles import normalize_toolset, normalize_workflow
from core.version import APP_VERSION, is_newer_version
from services.team_update import (
    can_self_update,
    exit_for_update,
    prepare_team_update,
    schedule_windows_replace,
)
from services import github_update
from services import hub_host

EXPORT_DIR = STORAGE_ROOT / "sync_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _json_setting(key: str, fallback: Any) -> Any:
    try:
        return json.loads(get_setting(key, json.dumps(fallback, ensure_ascii=False)))
    except json.JSONDecodeError:
        return fallback


def personalization_theme() -> dict[str, Any]:
    return {
        "accent": get_setting("ui_accent", "terracotta"),
        "density": get_setting("ui_density", "comfortable"),
        "scene": get_setting("ui_scene", "warm"),
        "motion": get_setting("ui_motion", "balanced"),
        "geometry": get_setting("ui_geometry", "soft"),
        "font_scale": get_setting("ui_font_scale", "normal"),
        "home_effect": get_setting("ui_home_effect", "orbits"),
        "home_motto": get_setting("ui_home_motto", "让科研更好玩一点"),
        "home_poem": get_setting(
            "ui_home_poem",
            "纸上得来终觉浅，绝知此事要躬行。——陆游",
        ),
        "poem_pool": _json_setting("ui_poem_pool", []),
        "site_name": get_setting("site_name", "科研系统"),
        "realm_names": normalize_realm_labels(_json_setting("realm_names", {})),
        "nav_labels": normalize_nav_labels(_json_setting("nav_labels", {})),
        "nav_layout": normalize_nav_layout(_json_setting("nav_layout", [])),
        "home_layout": _json_setting("home_layout", []),
        "review_popup": get_setting("review_popup", "1"),
    }


def register_online_routes(app, templates, context: Callable[..., dict[str, Any]], flash: Callable[[Request, str, str], None]):
    router = APIRouter()

    def enqueue_initial_state() -> None:
        with connect() as conn:
            profile = dict(conn.execute("SELECT * FROM player_profile WHERE id=1").fetchone())
            claim_row = conn.execute(
                "SELECT value FROM settings WHERE key='hub_initial_claim_uuid'"
            ).fetchone()
            scope_row = conn.execute(
                "SELECT value FROM settings WHERE key='hub_initial_claim_scope'"
            ).fetchone()
            hub_scope = hashlib.sha256(
                (
                    f"{get_setting('hub_url', '').strip().rstrip('/')}\0"
                    f"{get_setting('hub_api_token', '').strip()}"
                ).encode("utf-8")
            ).hexdigest()
            claim_uuid = str(claim_row["value"]).strip() if claim_row else ""
            saved_scope = str(scope_row["value"]).strip() if scope_row else ""
            if not claim_uuid or saved_scope != hub_scope:
                claim_uuid = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO settings(key,value) VALUES ('hub_initial_claim_uuid',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (claim_uuid,),
                )
                conn.execute(
                    """
                    INSERT INTO settings(key,value) VALUES ('hub_initial_claim_scope',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (hub_scope,),
                )
            inventory = [
                dict(item)
                for item in conn.execute(
                    "SELECT item_key,item_type,quantity,level,equipped FROM inventory_items ORDER BY item_type,item_key"
                )
            ]
            queue_event(
                conn,
                "initial_state_claim",
                {"balances": local_balances(conn), "inventory": inventory},
                event_uuid=claim_uuid,
            )
            queue_event(conn, "profile_updated", profile)
            queue_event(conn, "personalization_updated", personalization_theme())
            conn.commit()

    @router.get("/online", response_class=HTMLResponse, name="online_page")
    def online_page(request: Request):
        provider = get_setting("sync_provider", "disabled")
        hub_url = get_setting("hub_url", "")
        hub_token = get_setting("hub_api_token", "")
        backend = build_sync_backend(
            provider,
            hub_url,
            hub_token,
        )
        health = sync_health()
        with connect() as conn:
            recent = [dict(row) for row in conn.execute("SELECT * FROM online_sync_queue ORDER BY id DESC LIMIT 12")]
        url_ok, url_policy = validate_hub_url(hub_url) if hub_url else (True, "尚未配置中心地址")
        latest_release = cached_value("latest_release", None)
        update_available = bool(
            isinstance(latest_release, dict)
            and is_newer_version(str(latest_release.get("version", "")), APP_VERSION)
        )
        return templates.TemplateResponse(
            request=request,
            name="online.html",
            context=context(
                request,
                "online",
                sync_provider=provider,
                sync_capabilities=backend.capabilities.as_dict(),
                backend_options=all_backend_capabilities(),
                contract_version=SYNC_CONTRACT_VERSION,
                hub_url=hub_url,
                hub_token_configured=bool(hub_token.strip()),
                hub_url_ok=url_ok,
                hub_url_policy=url_policy,
                auto_sync=get_setting("hub_auto_sync", "0") == "1",
                theme=personalization_theme(),
                queue_stats=health["queue"],
                sync_health=health,
                recent=recent,
                last_sync=cached_value("last_sync", {}),
                last_error=cached_value("last_sync_error", {}),
                hub_state=cached_value("hub_state", {}),
                latest_release=latest_release,
                local_version=APP_VERSION,
                update_available=update_available,
                can_self_update=can_self_update(),
                github_update=_json_setting("github_update", {}),
                github_update_checked_at=get_setting("github_update_checked_at", ""),
                data_root=str(DATA_ROOT),
                hub_host=hub_host.status(),
            ),
        )

    @router.post("/online/hub-host/start", name="online_hub_host_start")
    def online_hub_host_start(request: Request, port: int = Form(5050)):
        try:
            host = hub_host.start(STORAGE_ROOT.parent, port=port)
        except OSError as exc:
            flash(request, f"同行会中心未能启动：{exc}", "error")
        else:
            flash(
                request,
                f"同行会已在本程序后台启动。成员填写 {host['lan_url']}；管理员界面会在下方“进入同行会”中按身份显示。",
            )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/hub-host/stop", name="online_hub_host_stop")
    def online_hub_host_stop(request: Request):
        hub_host.stop()
        flash(request, "已停止本机同行会中心；你的个人科研资料不受影响。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.get("/api/sync/capabilities", name="sync_capabilities")
    def sync_capabilities():
        provider = get_setting("sync_provider", "disabled")
        backend = build_sync_backend(
            provider,
            get_setting("hub_url", ""),
            get_setting("hub_api_token", ""),
        )
        return {
            "application": "科研系统",
            "local_version": APP_VERSION,
            "active_backend": backend.capabilities.as_dict(),
            "available_backends": all_backend_capabilities(),
            "data_policy": {
                "research_files": "local_only",
                "queued_while_disabled": False,
                "research_hub_ready": True,
                "android_client_supported": True,
                "cloud_v2_implemented": False,
            },
            "reliability_policy": sync_health()["policy"],
        }

    @router.post("/online/update/latest", name="online_update_latest")
    def online_update_latest(request: Request):
        release = cached_value("latest_release", None)
        if not isinstance(release, dict):
            flash(request, "尚未发现可用的同行会更新包，请先点击“立即同步”。", "error")
            return RedirectResponse("/online", status_code=303)
        if not is_newer_version(str(release.get("version", "")), APP_VERSION):
            flash(request, "当前版本不低于同行会记录的最新版本，无需更新。")
            return RedirectResponse("/online", status_code=303)
        try:
            package = prepare_team_update(
                hub_url=get_setting("hub_url", ""),
                token=get_setting("hub_api_token", ""),
                release_id=int(release["id"]),
                sha256=str(release["sha256"]),
            )
            schedule_windows_replace(package)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            flash(request, f"更新未启动：{exc}", "error")
            return RedirectResponse("/online", status_code=303)
        flash(request, "更新包已校验、个人数据库已备份；程序将在数秒后重启到新版。")
        exit_for_update()
        return RedirectResponse("/online", status_code=303)

    @router.post("/online/update/check", name="online_update_check")
    def online_update_check(request: Request):
        result = sync_now(force=True, pull_if_idle=True)
        if not result.get("ok"):
            flash(request, f"检查更新失败：{result.get('message', '请检查同行会连接。')}", "error")
        else:
            release = cached_value("latest_release", None)
            if isinstance(release, dict) and is_newer_version(str(release.get("version", "")), APP_VERSION):
                flash(request, f"发现 v{release['version']}，已验证为同行会更新包，可直接更新。")
            else:
                flash(request, "当前已是同行会记录的最新版本。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/update/github-check", name="online_update_github_check")
    def online_update_github_check(request: Request):
        try:
            release = github_update.latest_release()
        except github_update.GithubUpdateError as exc:
            set_setting("github_update", "")
            flash(request, f"GitHub 检查失败：{exc}", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        set_setting("github_update_checked_at", now_iso())
        if not release:
            set_setting("github_update", "")
            flash(request, "GitHub 上没有可用的正式发布版本。")
        elif release.get("newer"):
            set_setting("github_update", json.dumps(release, ensure_ascii=False))
            flash(
                request,
                f"GitHub 发现 v{release['version']}（当前 {APP_VERSION}）。"
                "更新只替换程序 EXE，不会改动你的资料。",
            )
        else:
            set_setting("github_update", "")
            flash(request, f"GitHub 上的 {release['version']} 不高于当前 {APP_VERSION}，无需更新。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/update/github-apply", name="online_update_github_apply")
    def online_update_github_apply(request: Request):
        if not can_self_update():
            flash(
                request,
                "自动替换只在已打包的 Windows EXE 中启用；源码模式请下载发布包手动替换。",
                "error",
            )
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        try:
            release = github_update.latest_release()
            if not release or not release.get("newer"):
                flash(request, "GitHub 上没有比当前更新的正式版本。")
                return RedirectResponse(request.url_for("online_page"), status_code=303)
            package = github_update.prepare_github_update(release)
            schedule_windows_replace(package)
        except (github_update.GithubUpdateError, OSError, ValueError, RuntimeError) as exc:
            flash(request, f"GitHub 更新未启动：{exc}", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        set_setting("github_update", "")
        flash(request, "已从 GitHub 校验更新包并备份数据库；程序将在数秒后重启到新版。")
        exit_for_update()
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/settings", name="online_settings_save")
    def online_settings_save(
        request: Request,
        sync_provider: str = Form("disabled"),
        hub_url: str = Form(""),
        hub_api_token: str = Form(""),
        auto_sync: str = Form(""),
        connect_now: str = Form(""),
    ):
        if sync_provider not in {"disabled", "legacy_hub"}:
            flash(request, "规模化云端仍是预留接口，当前不能启用。", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        url = hub_url.strip().rstrip("/")
        existing_token = get_setting("hub_api_token", "").strip()
        token = hub_api_token.strip() or existing_token
        if sync_provider == "legacy_hub":
            url_ok, url_message = validate_hub_url(url)
            if not url_ok:
                flash(request, url_message, "error")
                return RedirectResponse(request.url_for("online_page"), status_code=303)
            if not token:
                flash(request, "启用轻量同行会前必须填写 Token。", "error")
                return RedirectResponse(request.url_for("online_page"), status_code=303)
        set_setting("sync_provider", sync_provider)
        set_setting("hub_url", url)
        if hub_api_token.strip():
            set_setting("hub_api_token", hub_api_token.strip())
        set_setting(
            "hub_auto_sync",
            "1" if sync_provider == "legacy_hub" and auto_sync == "1" else "0",
        )
        if sync_provider == "legacy_hub" and connect_now == "1":
            enqueue_initial_state()
            result = sync_now(force=True)
            flash(
                request,
                (
                    f"{result['message']} 联机通道已开启，"
                    f"本次确认 {result.get('synced', 0)} 个状态事件。"
                ),
                "success" if result["ok"] else "error",
            )
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        flash(
            request,
            "联机扩展保持关闭，本机不会上传数据。"
            if sync_provider == "disabled"
            else "ResearchHub 设置已保存；点击“保存并联机”即可完成首次合并。",
        )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/connect", name="online_connect")
    def online_connect(request: Request):
        if get_setting("sync_provider", "disabled") != "legacy_hub":
            flash(request, "当前未启用联机后端。", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        enqueue_initial_state()
        result = sync_now(force=True)
        flash(request, result["message"], "success" if result["ok"] else "error")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/sync", name="online_sync_now")
    def online_sync_now(request: Request):
        result = sync_now(force=True)
        flash(request, f"{result['message']} 本次确认 {result.get('synced', 0)} 个事件。", "success" if result["ok"] else "error")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/test", name="online_connection_test")
    def online_connection_test(request: Request):
        result = test_connection()
        flash(request, result["message"], "success" if result["ok"] else "error")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/retry-paused", name="online_retry_paused")
    def online_retry_paused(request: Request):
        count = retry_paused_events()
        if not count:
            flash(request, "当前没有暂停事件。")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        result = sync_now(force=True)
        flash(
            request,
            f"已恢复 {count} 个事件。{result['message']}",
            "success" if result["ok"] else "error",
        )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/theme", name="online_theme_save")
    def online_theme_save(
        request: Request,
        accent: str = Form("terracotta"), density: str = Form("comfortable"),
        scene: str = Form("warm"), home_motto: str = Form(""), home_poem: str = Form(""),
        poem_pool: str = Form(""), motion: str = Form("balanced"),
        geometry: str = Form("soft"), font_scale: str = Form("normal"),
        home_effect: str = Form("orbits"),
    ):
        allowed = {
            "accent": {
                "terracotta", "amber", "sage", "ink", "cobalt", "plum", "coral"
            },
            "density": {"focus", "comfortable", "compact", "spacious", "ultra"},
            "scene": {"warm", "forest", "paper", "night", "aurora", "dusk"},
            "motion": {"reduced", "balanced", "lively"},
            "geometry": {"soft", "sharp", "orbital"},
            "font_scale": {"compact", "normal", "large"},
            "home_effect": {"orbits", "prism", "constellation", "none"},
        }
        values = {
            "accent": accent if accent in allowed["accent"] else "terracotta",
            "density": density if density in allowed["density"] else "comfortable",
            "scene": scene if scene in allowed["scene"] else "warm",
            "motion": motion if motion in allowed["motion"] else "balanced",
            "geometry": geometry if geometry in allowed["geometry"] else "soft",
            "font_scale": (
                font_scale if font_scale in allowed["font_scale"] else "normal"
            ),
            "home_effect": (
                home_effect
                if home_effect in allowed["home_effect"]
                else "orbits"
            ),
            "home_motto": home_motto.strip()[:80] or "让科研更好玩一点",
            "home_poem": home_poem.strip()[:120] or "纸上得来终觉浅，绝知此事要躬行。——陆游",
            "poem_pool": [line.strip()[:120] for line in poem_pool.splitlines() if line.strip()][:366],
        }
        set_setting("ui_accent", values["accent"])
        set_setting("ui_density", values["density"])
        set_setting("ui_scene", values["scene"])
        set_setting("ui_motion", values["motion"])
        set_setting("ui_geometry", values["geometry"])
        set_setting("ui_font_scale", values["font_scale"])
        set_setting("ui_home_effect", values["home_effect"])
        set_setting("ui_home_motto", values["home_motto"])
        set_setting("ui_home_poem", values["home_poem"])
        set_setting("ui_poem_pool", json.dumps(values["poem_pool"], ensure_ascii=False))
        with connect() as conn:
            queue_event(conn, "personalization_updated", personalization_theme())
            conn.commit()
        best_effort_sync()
        flash(request, "个性化方案已保存；新版本可直接导入。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.get("/online/personalization/export", name="online_personalization_export")
    def personalization_export():
        with connect() as conn:
            profile = dict(conn.execute("SELECT * FROM player_profile WHERE id=1").fetchone())
            workspaces = []
            for row in conn.execute(
                """
                SELECT workspace_key,name,icon,module,description,accent,sort_order,active,
                       pinned_home,objective,workflow_json,toolset_json
                FROM workspaces ORDER BY sort_order,id
                """
            ):
                item = dict(row)
                module = str(item["module"])
                item["workflow"] = normalize_workflow(item.pop("workflow_json", "[]"), module)
                item["tools"] = normalize_toolset(item.pop("toolset_json", "[]"), module)
                workspaces.append(item)
        payload = {
            "format": "research-cultivation-personalization-v8",
            "schema_version": 8,
            "exported_at": now_iso(),
            "theme": personalization_theme(),
            "profile": {k: profile[k] for k in ("display_name", "title", "bio", "skills", "capabilities", "goals", "avatar_symbol")},
            "avatar_image": export_avatar_payload(),
            "workspaces": workspaces,
        }
        path = EXPORT_DIR / f"personalization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return FileResponse(path, filename=path.name, media_type="application/json")

    @router.post("/online/personalization/import", name="online_personalization_import")
    async def personalization_import(request: Request, file: UploadFile = File(...)):
        try:
            data = json.loads((await file.read()).decode("utf-8"))
            if data.get("format") not in {
                "research-cultivation-personalization-v1",
                "research-cultivation-personalization-v2",
                "research-cultivation-personalization-v3",
                "research-cultivation-personalization-v4",
                "research-cultivation-personalization-v5",
                "research-cultivation-personalization-v6",
                "research-cultivation-personalization-v7",
                "research-cultivation-personalization-v8",
            }:
                raise ValueError("不是受支持的个性化包")
            theme = data.get("theme", {})
            profile = data.get("profile", {})
            for source, key, default in [
                ("accent", "ui_accent", "terracotta"), ("density", "ui_density", "comfortable"),
                ("scene", "ui_scene", "warm"), ("home_motto", "ui_home_motto", "让科研更好玩一点"),
                ("home_poem", "ui_home_poem", "纸上得来终觉浅，绝知此事要躬行。——陆游"),
                ("motion", "ui_motion", "balanced"), ("geometry", "ui_geometry", "soft"),
                ("font_scale", "ui_font_scale", "normal"), ("home_effect", "ui_home_effect", "orbits"),
            ]:
                set_setting(key, str(theme.get(source, default)))
            if isinstance(theme.get("poem_pool"), list):
                set_setting(
                    "ui_poem_pool",
                    json.dumps(
                        [str(item).strip()[:120] for item in theme["poem_pool"] if str(item).strip()][:366],
                        ensure_ascii=False,
                    ),
                )
            if isinstance(theme.get("realm_names"), (list, dict)):
                set_setting(
                    "realm_names",
                    json.dumps(normalize_realm_labels(theme["realm_names"]), ensure_ascii=False),
                )
            if isinstance(theme.get("nav_labels"), dict):
                set_setting(
                    "nav_labels",
                    json.dumps(normalize_nav_labels(theme["nav_labels"]), ensure_ascii=False),
                )
            if isinstance(theme.get("nav_layout"), list):
                set_setting(
                    "nav_layout",
                    json.dumps(normalize_nav_layout(theme["nav_layout"]), ensure_ascii=False),
                )
            if isinstance(theme.get("home_layout"), list):
                cleaned_home_layout = []
                seen_home_blocks = set()
                for item in theme["home_layout"]:
                    key = str(item.get("key", "")) if isinstance(item, dict) else ""
                    if key not in {"gate", "search", "shortcuts", "workbench", "continuity"} or key in seen_home_blocks:
                        continue
                    try:
                        span = max(4, min(12, int(item.get("span", 12))))
                    except (TypeError, ValueError):
                        span = 12
                    cleaned_home_layout.append({"key": key, "span": span})
                    seen_home_blocks.add(key)
                set_setting("home_layout", json.dumps(cleaned_home_layout, ensure_ascii=False))
            if str(theme.get("site_name", "")).strip():
                imported_site_name = str(theme["site_name"]).strip()[:60]
                set_setting(
                    "site_name",
                    "科研系统"
                    if imported_site_name == "问道科研"
                    else imported_site_name,
                )
            if str(theme.get("review_popup", "1")) in {"0", "1"}:
                set_setting("review_popup", str(theme.get("review_popup", "1")))
            with connect() as conn:
                conn.execute(
                    """UPDATE player_profile SET display_name=?,title=?,bio=?,skills=?,capabilities=?,goals=?,avatar_symbol=?,updated_at=? WHERE id=1""",
                    (
                        str(profile.get("display_name", "修士")), str(profile.get("title", "")),
                        str(profile.get("bio", "")), str(profile.get("skills", "")),
                        str(profile.get("capabilities", "")), str(profile.get("goals", "")),
                        str(profile.get("avatar_symbol", "道"))[:2] or "道", now_iso(),
                    ),
                )
                queue_event(conn, "profile_updated", profile)
                queue_event(conn, "personalization_updated", theme)
                incoming_workspaces = data.get("workspaces", [])
                if isinstance(incoming_workspaces, list):
                    for index, item in enumerate(incoming_workspaces[:100]):
                        if not isinstance(item, dict):
                            continue
                        workspace_key = str(item.get("workspace_key", "")).strip()[:80]
                        name = str(item.get("name", "")).strip()[:40]
                        if not workspace_key or not name:
                            continue
                        module = str(item.get("module", "knowledge"))
                        if module not in {
                            "knowledge", "experiments", "simulations", "datasets",
                            "ml", "md", "comsol",
                        }:
                            module = "knowledge"
                        accent = str(item.get("accent", "clay"))
                        if accent not in {"clay", "sage", "ink", "amber"}:
                            accent = "clay"
                        workflow = normalize_workflow(
                            item.get("workflow", item.get("workflow_json", "[]")),
                            module,
                        )
                        toolset = normalize_toolset(
                            item.get("tools", item.get("toolset_json", "[]")),
                            module,
                        )
                        conn.execute(
                            """
                            INSERT INTO workspaces(
                                workspace_key,name,icon,module,description,accent,sort_order,active,
                                pinned_home,objective,workflow_json,toolset_json,created_at,updated_at
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(workspace_key) DO UPDATE SET
                                name=excluded.name,icon=excluded.icon,module=excluded.module,
                                description=excluded.description,accent=excluded.accent,
                                sort_order=excluded.sort_order,active=excluded.active,
                                pinned_home=excluded.pinned_home,objective=excluded.objective,
                                workflow_json=excluded.workflow_json,toolset_json=excluded.toolset_json,
                                updated_at=excluded.updated_at
                            """,
                            (
                                workspace_key,
                                name,
                                str(item.get("icon", "研")).strip()[:2] or "研",
                                module,
                                str(item.get("description", "")).strip()[:240],
                                accent,
                                int(item.get("sort_order", (index + 1) * 10)),
                                1 if int(item.get("active", 1)) else 0,
                                1 if int(item.get("pinned_home", 0)) else 0,
                                str(item.get("objective", "")).strip()[:300],
                                json.dumps(workflow, ensure_ascii=False),
                                json.dumps(toolset, ensure_ascii=False),
                                now_iso(),
                                now_iso(),
                            ),
                        )
                    conn.execute(
                        "UPDATE workspaces SET pinned_home=0 WHERE active=0"
                    )
                    pinned_rows = conn.execute(
                        """
                        SELECT id FROM workspaces
                        WHERE active=1 AND pinned_home=1
                        ORDER BY sort_order,id
                        """
                    ).fetchall()
                    for row in pinned_rows[6:]:
                        conn.execute(
                            "UPDATE workspaces SET pinned_home=0 WHERE id=?",
                            (row["id"],),
                        )
                conn.commit()
            if data.get("avatar_image"):
                import_avatar_payload(data["avatar_image"])
            flash(request, "个性化包已导入。")
        except Exception as exc:
            flash(request, f"导入失败：{exc}", "error")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    app.include_router(router)
