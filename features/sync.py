from __future__ import annotations

import urllib.error

import hashlib
import json
import re
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
    reveal_file,
    reveal_path,
    schedule_windows_replace,
)
from services import github_update
from services.team_update import last_apply_log
from services import headscale_vpn, hub_host, hub_identity, hub_realm, release_feed

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
        # 版本包信息按身份现取：管理员读本机中心，成员问对方中心；都失败才退回同步缓存。
        latest_release = release_feed.latest_release()
        update_available = bool(
            isinstance(latest_release, dict)
            and is_newer_version(str(latest_release.get("version", "")), APP_VERSION)
        )
        host_status = hub_host.status()
        identity = hub_identity.resolve(host_status)
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
                github_releases_url=f"https://github.com/{github_update.GITHUB_REPO}/releases",
                github_update_checked_at=get_setting("github_update_checked_at", ""),
                update_log_tail=last_apply_log(),
                data_root=str(DATA_ROOT),
                hub_host=host_status,
                identity=identity,
                headnet=headscale_vpn.status(),
            ),
        )

    @router.get("/online/enter", name="online_enter")
    def online_enter(request: Request):
        """「进入同行会」的唯一入口：按当前身份打开对应的那一份中心。"""
        identity = hub_identity.resolve()
        return RedirectResponse(identity["entry_url"], status_code=303)

    @router.post("/online/releases/download", name="online_release_download")
    def online_release_download(request: Request):
        """把当前中心的版本包取到本机并校验（只落盘，不替换程序）。"""
        release = release_feed.latest_release()
        if not release:
            flash(
                request,
                "还没有拿到版本包信息。管理员请先在「版本中心」上传；成员请先检查与中心地址、Token 是否可用。",
                "error",
            )
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        source = release_feed.local_source(release)
        if source is not None:
            # 中心身份：包本来就在本机，原地定位并选中，不复制第二份。
            reveal_file(source)
            flash(
                request,
                f"版本包 v{release.get('version', '')} 就在本机中心存储中，已在文件夹中定位：{source}",
            )
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        try:
            package = release_feed.fetch_package(release)
        except (OSError, ValueError, RuntimeError) as exc:
            flash(request, f"版本包下载失败：{exc}", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        where = str(release.get("center_url") or "中心")
        flash(
            request,
            f"版本包 v{release.get('version', '')}（来自{where}）已取到本机并通过 SHA-256 校验：{package}。"
            "可用「打开版本包所在文件夹」取用。",
        )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/releases/reveal", name="online_release_reveal")
    def online_release_reveal(request: Request):
        release = release_feed.latest_release()
        source = release_feed.local_source(release)
        if source is not None:
            reveal_file(source)
            flash(request, f"已在本机中心存储中定位版本包：{source}")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        folder = STORAGE_ROOT / "team_updates" / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        reveal_path(folder)
        flash(request, f"已打开版本包文件夹：{folder}")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/hub-host/start", name="online_hub_host_start")
    def online_hub_host_start(request: Request, port: int = Form(5050)):
        try:
            host = hub_host.start(STORAGE_ROOT.parent, port=port)
        except (OSError, RuntimeError) as exc:
            flash(request, f"同行会中心未能启动：{exc}", "error")
        else:
            access_urls = host.get("access_urls") or []
            preferred = str(host.get("preferred_url") or host.get("local_url") or "")
            if access_urls:
                flash(
                    request,
                    f"同行会已启动。成员可尝试 {preferred}；有线、无线和 VPN 地址已列在下方，"
                    "请选择双方都能访问的地址。",
                )
            else:
                flash(
                    request,
                    "同行会已在本机启动，但未检测到局域网/VPN 地址。"
                    "请在管理控制台填写固定的 VPN 或公网连接地址。",
                    "error",
                )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/headnet/save", name="online_headnet_save")
    def online_headnet_save(
        request: Request,
        control_url: str = Form(""),
        auth_key: str = Form(""),
        hostname: str = Form("wendao-node"),
        peer: str = Form(""),
        port: int = Form(5050),
        peer_port: int = Form(5050),
    ):
        try:
            headscale_vpn.save_config(
                control_url=control_url,
                auth_key=auth_key,
                hostname=hostname,
                peer=peer,
                center_port=port,
                peer_port=peer_port,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            flash(request, f"Headscale 配置未保存：{exc}", "error")
        else:
            flash(request, "Headscale 控制端配置已保存。预授权密钥只保存在本机数据库。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    def _headnet_save_form(
        control_url: str,
        auth_key: str,
        hostname: str,
        peer: str,
        port: int,
        peer_port: int,
    ) -> None:
        headscale_vpn.save_config(
            control_url=control_url,
            auth_key=auth_key,
            hostname=hostname,
            peer=peer,
            center_port=port,
            peer_port=peer_port,
        )

    @router.post("/online/headnet/start-center", name="online_headnet_start_center")
    def online_headnet_start_center(
        request: Request,
        control_url: str = Form(""),
        auth_key: str = Form(""),
        hostname: str = Form("wendao-hub"),
        peer: str = Form(""),
        port: int = Form(5050),
        peer_port: int = Form(5050),
    ):
        try:
            _headnet_save_form(control_url, auth_key, hostname, peer, port, peer_port)
            hub = hub_host.status()
            if not hub.get("running"):
                hub = hub_host.start(STORAGE_ROOT.parent, port=port)
            result = headscale_vpn.start_center(
                hub_port=int(hub["port"]),
                hostname_override=hostname,
            )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            flash(request, f"Headscale 中心发布失败：{exc}", "error")
        else:
            flash(
                request,
                f"中心已加入 Headscale 虚拟网络：{result.get('virtual_url', '')}。"
                "邀请码会自动使用这个地址；请确保 Headscale/DERP 与 ACL 已允许成员访问。",
            )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/headnet/start-member", name="online_headnet_start_member")
    def online_headnet_start_member(
        request: Request,
        control_url: str = Form(""),
        auth_key: str = Form(""),
        hostname: str = Form("wendao-member"),
        peer: str = Form(""),
        port: int = Form(5050),
        peer_port: int = Form(5050),
    ):
        try:
            _headnet_save_form(control_url, auth_key, hostname, peer, port, peer_port)
            result = headscale_vpn.start_member(
                peer_override=peer,
                peer_port=peer_port,
                hostname_override=hostname,
            )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            flash(request, f"Headscale 成员连接失败：{exc}", "error")
        else:
            flash(
                request,
                f"已加入 Headscale 虚拟网络；本地入口：{result.get('local_url', '')}。"
                "点击下方“打开虚拟网络同行会”完成登录或邀请码注册。",
            )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/headnet/stop", name="online_headnet_stop")
    def online_headnet_stop(request: Request):
        headscale_vpn.stop()
        flash(request, "Headscale 虚拟网络连接已停止。局域网连接不会受影响。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/realm/push", name="online_realm_push")
    def online_realm_push(request: Request):
        """把完整境界名同步到中心展示（只推快照）。"""
        try:
            result = hub_realm.sync_if_due(force=True, raise_errors=True)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            flash(request, f"境界同步失败：{exc}", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        flash(request, f"已把境界「{result.get('realm_name', '')}」同步到中心。")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/headnet/auto", name="online_headnet_auto")
    def online_headnet_auto(request: Request):
        """一键组网：按当前身份自动选中心/成员模式，复用已保存配置。"""
        identity = hub_identity.resolve()
        role = "center" if identity["role"] == hub_identity.ROLE_HOST else "member"
        try:
            result = headscale_vpn.auto_connect(role)
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            flash(request, f"自动组网未完成：{exc}", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        target = result.get("virtual_url") or result.get("local_url") or ""
        flash(
            request,
            f"{result.get('message', '已接入虚拟网络。')}"
            f"当前以{'中心' if role == 'center' else '成员'}身份接入"
            + (f"，入口 {target}" if target else "")
            + "。下次启动会自动恢复。",
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
        release = release_feed.latest_release()
        if not release:
            flash(request, "尚未发现可用的同行会更新包，请先点击「检查新版本」。", "error")
            return RedirectResponse("/online", status_code=303)
        if release.get("source") == "local":
            flash(
                request,
                "你当前就是中心，本机已持有该版本包；用「下载版本包到本机」即可取用，无需从中心更新自己。",
                "error",
            )
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

    @router.post("/online/verify-package", name="online_verify_package")
    def online_verify_package(request: Request, package: UploadFile = File(...), expected: str = Form("")):
        """手动下载的更新包先校验 SHA-256 再替换 EXE，拦截被截断的下载。"""
        digest = hashlib.sha256()
        total = 0
        limit = 900 * 1024 * 1024
        name = package.filename or "package"
        while chunk := package.file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                flash(request, f"文件超过 900 MB，已停止校验：{name}", "error")
                return RedirectResponse(request.url_for("online_page"), status_code=303)
            digest.update(chunk)
        computed = digest.hexdigest()
        tokens = re.findall(r"[0-9a-fA-F]{64}", expected or "")
        if tokens:
            ok = computed == tokens[0].lower()
            verdict = "一致，文件完整。" if ok else "不一致：文件可能损坏或下载被截断，请勿替换 EXE，重新下载后再校验。"
            flash(request, f"{name} · SHA-256 {computed} · 与预期比对：{verdict}", "success" if ok else "error")
        else:
            flash(
                request,
                f"{name} · SHA-256：{computed}（{total} 字节）。"
                "请与发布页 SHA256SUMS.txt 中的对应行比对；也可把该行粘贴到预期框内自动比对。",
                "success",
            )
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
            release = github_update.resolve_apply_release()
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
        if connect_now == "1" and sync_provider == "disabled" and url and token:
            # 填了地址 + Token 还点了“保存并联机”，意图已经明确是当成员；
            # 不再要求先在下拉框里手动切一次模式。
            sync_provider = "legacy_hub"
        # 非敏感输入先落库：即使校验失败，用户也不必重新填写地址/模式。
        set_setting("sync_provider", sync_provider)
        set_setting("hub_url", url)
        if hub_api_token.strip():
            set_setting("hub_api_token", hub_api_token.strip())
        set_setting(
            "hub_auto_sync",
            "1" if sync_provider == "legacy_hub" and auto_sync == "1" else "0",
        )
        if sync_provider == "legacy_hub":
            url_ok, url_message = validate_hub_url(url)
            if not url_ok:
                flash(
                    request,
                    f"{url_message} 已暂存你填写的地址与模式（Token 不会明文回显），修正后重新保存即可。",
                    "error",
                )
                return RedirectResponse(request.url_for("online_page"), status_code=303)
            if not token:
                flash(
                    request,
                    "启用轻量同行会前必须填写 Token（从“我的联机主页”复制）。地址与模式已暂存。",
                    "error",
                )
                return RedirectResponse(request.url_for("online_page"), status_code=303)
        if sync_provider == "legacy_hub" and connect_now == "1":
            enqueue_initial_state()
            result = sync_now(force=True)
            if result["ok"]:
                hub_realm.sync_if_due()
            identity = hub_identity.resolve()
            flash(
                request,
                (
                    f"{result['message']} 联机通道已开启，本次确认 {result.get('synced', 0)} 个状态事件。"
                    f"当前身份：{identity['badge']}；「进入同行会」将打开 {identity['center_url'] or '本机中心'}。"
                ),
                "success" if result["ok"] else "error",
            )
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        if sync_provider == "disabled":
            flash(request, "已保存：联机保持关闭，本机不会上传数据；中心地址与 Token 已留在本机备用。", "success")
        else:
            flash(
                request,
                "已保存（未联机）：中心地址与 Token 已写入本机数据库；"
                "需要同步时点击“保存并联机”或“立即同步”。",
                "success",
            )
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/connect", name="online_connect")
    def online_connect(request: Request):
        if get_setting("sync_provider", "disabled") != "legacy_hub":
            flash(request, "当前未启用联机后端。", "error")
            return RedirectResponse(request.url_for("online_page"), status_code=303)
        enqueue_initial_state()
        result = sync_now(force=True)
        if result["ok"]:
            hub_realm.sync_if_due()  # 联机成功后顺带把境界推过去，失败静默
        flash(request, result["message"], "success" if result["ok"] else "error")
        return RedirectResponse(request.url_for("online_page"), status_code=303)

    @router.post("/online/sync", name="online_sync_now")
    def online_sync_now(request: Request):
        result = sync_now(force=True)
        realm_pushed = hub_realm.sync_if_due() if result["ok"] else None
        extra = f"境界已同步为「{realm_pushed['realm_name']}」。" if realm_pushed else ""
        flash(
            request,
            f"{result['message']} 本次确认 {result.get('synced', 0)} 个事件。{extra}",
            "success" if result["ok"] else "error",
        )
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
