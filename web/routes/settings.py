from __future__ import annotations

from fastapi import APIRouter

from core.runtime_paths import DATA_ROOT
from web.runtime import (
    APP_VERSION,
    Any,
    BACKUP_DIR,
    DB_PATH,
    DEFAULT_NAV_LABELS,
    DEFAULT_POEMS,
    DELIVERY_DIR,
    FOUNDATION_DIR,
    FileResponse,
    Form,
    HTMLResponse,
    KINDS,
    NOTE_IMAGE_DIR,
    PROFILE_DIR,
    Path,
    REALM_STAGES,
    Request,
    SIMULATION_DIR,
    UPLOAD_DIR,
    USER_CONFIG_DIR,
    bleach,
    configured_poem_pool,
    configured_realm_names,
    connect,
    context,
    datetime,
    default_realm_labels,
    domains,
    flash,
    get_setting,
    html,
    json,
    log_activity,
    navigation_editor_layout,
    navigation_labels,
    navigation_layout,
    normalize_nav_layout,
    normalize_realm_labels,
    now_iso,
    provider_status,
    re,
    redirect,
    secure_filename,
    set_setting,
    shutil,
    sqlite3,
    templates,
    zipfile,
)

app = APIRouter()

@app.get("/settings", response_class=HTMLResponse, name="settings_page")
def settings_get(request: Request):
    realm_labels = configured_realm_names()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context(
            request,
            "settings",
            current_site_name=get_setting("site_name", "科研系统"),
            current_researcher_name=get_setting("researcher_name", "修士"),
            domain_text="\n".join(domains()),
            ai_mode=get_setting("ai_mode", "offline"),
            ai_endpoint=get_setting("ai_endpoint", "http://127.0.0.1:11434/api/generate"),
            ai_model=get_setting("ai_model", "qwen2.5:7b"),
            ai_status=provider_status(),
            realm_names_text="\n".join(
                f"{stage.key}={realm_labels[stage.key]}" for stage in REALM_STAGES
            ),
            nav_labels_text="\n".join(f"{key}={value}" for key, value in navigation_labels().items()),
            nav_layout_editor=navigation_editor_layout(),
            nav_layout_json=json.dumps(navigation_layout(), ensure_ascii=False),
            review_popup=get_setting("review_popup", "1") == "1",
            gamification=get_setting("gamification", "off") == "on",
            poem_pool_text="\n".join(configured_poem_pool()),
            current_ui_accent=get_setting("ui_accent", "terracotta"),
            current_ui_density=get_setting("ui_density", "comfortable"),
            current_ui_scene=get_setting("ui_scene", "warm"),
            current_ui_motion=get_setting("ui_motion", "balanced"),
            current_ui_geometry=get_setting("ui_geometry", "soft"),
            current_ui_font_scale=get_setting("ui_font_scale", "normal"),
            current_ui_home_effect=get_setting("ui_home_effect", "orbits"),
            current_ui_home_motto=get_setting(
                "ui_home_motto", "让科研更好玩一点"
            ),
            portable_version=get_setting("portable_version", APP_VERSION),
            data_root=str(DATA_ROOT),
        ),
    )


@app.post("/settings", name="settings_post")
def settings_post(
    request: Request,
    site_name: str = Form("科研系统"),
    researcher_name: str = Form("修士"),
    domains_text: str = Form("", alias="domains"),
    ai_mode: str = Form("offline"),
    ai_endpoint: str = Form("http://127.0.0.1:11434/api/generate"),
    ai_model: str = Form("qwen2.5:7b"),
    realm_names: str = Form(""),
    nav_labels: str = Form(""),
    nav_layout: str = Form(""),
    review_popup: str = Form(""),
    gamification: str = Form(""),
    poem_pool: str = Form(""),
    home_poem: str = Form(""),
    ui_accent: str = Form("terracotta"),
    ui_density: str = Form("comfortable"),
    ui_scene: str = Form("warm"),
    ui_motion: str = Form("balanced"),
    ui_geometry: str = Form("soft"),
    ui_font_scale: str = Form("normal"),
    ui_home_effect: str = Form("orbits"),
    ui_home_motto: str = Form(""),
):
    domain_list = [x.strip() for x in domains_text.splitlines() if x.strip()]
    if "未分类" not in domain_list:
        domain_list.append("未分类")
    set_setting("site_name", site_name.strip() or "科研系统")
    set_setting("researcher_name", researcher_name.strip() or "修士")
    set_setting("domains", json.dumps(list(dict.fromkeys(domain_list)), ensure_ascii=False))
    set_setting("ai_mode", ai_mode if ai_mode in {"offline", "ollama", "openai"} else "offline")
    set_setting("ai_endpoint", ai_endpoint.strip() or "http://127.0.0.1:11434/api/generate")
    set_setting("ai_model", ai_model.strip() or "qwen2.5:7b")
    realm_lines = [line.strip() for line in realm_names.splitlines() if line.strip()]
    if any("=" in line for line in realm_lines):
        merged_realms = default_realm_labels()
        for line in realm_lines:
            if "=" not in line:
                continue
            key, value = [part.strip() for part in line.split("=", 1)]
            if key in merged_realms and value:
                merged_realms[key] = value[:30]
    else:
        merged_realms = normalize_realm_labels(realm_lines)
    set_setting("realm_names", json.dumps(merged_realms, ensure_ascii=False))
    parsed_nav = dict(DEFAULT_NAV_LABELS)
    for line in nav_labels.splitlines():
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key in parsed_nav and value:
            parsed_nav[key] = value[:24]
    set_setting("nav_labels", json.dumps(parsed_nav, ensure_ascii=False))
    try:
        parsed_nav_layout = json.loads(nav_layout)
    except json.JSONDecodeError:
        parsed_nav_layout = navigation_layout()
        flash(request, "导航布局格式异常，已保留原来的顺序与显示状态。", "error")
    set_setting(
        "nav_layout",
        json.dumps(normalize_nav_layout(parsed_nav_layout), ensure_ascii=False),
    )
    set_setting("review_popup", "1" if review_popup == "1" else "0")
    gamification_on = gamification == "1"
    set_setting("gamification", "on" if gamification_on else "off")
    if gamification_on:
        # Turning the switch on must actually reveal something; the decorative
        # items stay off by default in a fresh 1.0 install.
        from web.runtime import GAMIFICATION_ITEMS

        layout = navigation_layout()
        for group in layout:
            for item in group["items"]:
                if item["key"] in GAMIFICATION_ITEMS:
                    item["visible"] = True
        set_setting("nav_layout", json.dumps(normalize_nav_layout(layout), ensure_ascii=False))
    custom_poems = [
        line.strip()[:120]
        for line in poem_pool.splitlines()
        if line.strip()
    ][:366]
    if not custom_poems and home_poem.strip():
        custom_poems = [home_poem.strip()[:120]]
    set_setting("ui_poem_pool", json.dumps(custom_poems, ensure_ascii=False))
    set_setting("ui_home_poem", custom_poems[0] if custom_poems else DEFAULT_POEMS[0])
    visual_values = {
        "ui_accent": (
            ui_accent
            if ui_accent
            in {"terracotta", "amber", "sage", "ink", "cobalt", "plum", "coral"}
            else "terracotta"
        ),
        "ui_density": (
            ui_density
            if ui_density in {"focus", "comfortable", "compact", "spacious", "ultra"}
            else "comfortable"
        ),
        "ui_scene": (
            ui_scene
            if ui_scene in {"warm", "forest", "paper", "night", "aurora", "dusk"}
            else "warm"
        ),
        "ui_motion": (
            ui_motion
            if ui_motion in {"reduced", "balanced", "lively"}
            else "balanced"
        ),
        "ui_geometry": (
            ui_geometry
            if ui_geometry in {"soft", "sharp", "orbital"}
            else "soft"
        ),
        "ui_font_scale": (
            ui_font_scale
            if ui_font_scale in {"compact", "normal", "large"}
            else "normal"
        ),
        "ui_home_effect": (
            ui_home_effect
            if ui_home_effect in {"orbits", "prism", "constellation", "none"}
            else "orbits"
        ),
        "ui_home_motto": (
            ui_home_motto.strip()[:80] or "让科研更好玩一点"
        ),
    }
    for key, value in visual_values.items():
        set_setting(key, value)
    from features.sync import personalization_theme
    from services.online_sync import best_effort_sync, queue_event

    with connect() as conn:
        queue_event(conn, "personalization_updated", personalization_theme())
        conn.commit()
    best_effort_sync()
    flash(request, "设置已保存。")
    return redirect("settings_page", request)


@app.get("/export/json", name="export_json")
def export_json(request: Request):
    with connect() as conn:
        entries = [dict(row) for row in conn.execute("SELECT * FROM entries ORDER BY id")]
        activities = [dict(row) for row in conn.execute("SELECT * FROM activities ORDER BY id")]
        quests = [dict(row) for row in conn.execute("SELECT * FROM quests ORDER BY id")]
        experiments = [dict(row) for row in conn.execute("SELECT * FROM experiments ORDER BY id")]
        simulations = [dict(row) for row in conn.execute("SELECT * FROM simulations ORDER BY id")]
        simulation_files = [dict(row) for row in conn.execute("SELECT * FROM simulation_files ORDER BY id")]
        workspaces = [dict(row) for row in conn.execute("SELECT * FROM workspaces ORDER BY sort_order,id")]
        research_projects = [dict(row) for row in conn.execute("SELECT * FROM research_projects ORDER BY updated_at DESC,id")]
        project_workspaces = [dict(row) for row in conn.execute("SELECT * FROM project_workspaces ORDER BY project_id,is_primary DESC,workspace_id")]
        project_milestones = [dict(row) for row in conn.execute("SELECT * FROM project_milestones ORDER BY project_id,sort_order,id")]
        project_cases = [dict(row) for row in conn.execute("SELECT * FROM project_cases ORDER BY project_id,created_at,id")]
        project_updates = [dict(row) for row in conn.execute("SELECT * FROM project_updates ORDER BY project_id,created_at,id")]
        career_moments = [dict(row) for row in conn.execute("SELECT * FROM career_moments ORDER BY occurred_on,id")]
        research_tracks = [dict(row) for row in conn.execute("SELECT * FROM research_tracks ORDER BY sort_order,id")]
        research_plan_items = [dict(row) for row in conn.execute("SELECT * FROM research_plan_items ORDER BY track_id,sort_order,id")]
        research_folders = [dict(row) for row in conn.execute("SELECT * FROM research_folders ORDER BY id")]
        research_folder_files = [dict(row) for row in conn.execute("SELECT * FROM research_folder_files ORDER BY folder_id,relative_path")]
        mission_deliveries = [dict(row) for row in conn.execute("SELECT * FROM mission_deliveries ORDER BY id")]
        mission_delivery_files = [dict(row) for row in conn.execute("SELECT * FROM mission_delivery_files ORDER BY delivery_id,relative_path")]
        asset_transactions = [dict(row) for row in conn.execute("SELECT * FROM asset_transactions ORDER BY id")]
        inventory_items = [dict(row) for row in conn.execute("SELECT * FROM inventory_items ORDER BY id")]
        player_profile = [dict(row) for row in conn.execute("SELECT * FROM player_profile ORDER BY id")]
        easter_eggs = [dict(row) for row in conn.execute("SELECT * FROM easter_eggs ORDER BY egg_key")]
        review_sources = [dict(row) for row in conn.execute("SELECT * FROM review_sources ORDER BY id")]
        review_sessions = [dict(row) for row in conn.execute("SELECT * FROM review_sessions ORDER BY id")]
        review_answers = [dict(row) for row in conn.execute("SELECT * FROM review_answers ORDER BY id")]
        realm_tribulations = [dict(row) for row in conn.execute("SELECT * FROM realm_tribulations ORDER BY id")]
        special_tasks = [dict(row) for row in conn.execute("SELECT * FROM special_tasks ORDER BY id")]
        herb_inventory = [dict(row) for row in conn.execute("SELECT * FROM herb_inventory ORDER BY grade")]
        settings_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT key,value FROM settings
                WHERE key NOT IN (
                    'hub_api_token','hub_initial_claim_uuid','hub_initial_claim_scope'
                )
                ORDER BY key
                """
            )
        ]
    path = BACKUP_DIR / f"research_os_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "entries": entries, "activities": activities, "quests": quests, "experiments": experiments,
        "simulations": simulations, "simulation_files": simulation_files,
        "workspaces": workspaces,
        "research_projects": research_projects,
        "project_workspaces": project_workspaces,
        "project_milestones": project_milestones,
        "project_cases": project_cases,
        "project_updates": project_updates,
        "career_moments": career_moments,
        "research_tracks": research_tracks, "research_plan_items": research_plan_items,
        "research_folders": research_folders, "research_folder_files": research_folder_files,
        "mission_deliveries": mission_deliveries, "mission_delivery_files": mission_delivery_files,
        "asset_transactions": asset_transactions, "inventory_items": inventory_items,
        "player_profile": player_profile, "easter_eggs": easter_eggs,
        "review_sources": review_sources, "review_sessions": review_sessions,
        "review_answers": review_answers, "realm_tribulations": realm_tribulations,
        "special_tasks": special_tasks,
        "herb_inventory": herb_inventory, "settings": settings_rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(path, media_type="application/json", filename=path.name)


def _plain_export_text(value: str, content_format: str = "plain") -> str:
    text = value or ""
    if content_format == "rich":
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(
            r"</(?:p|div|li|h[1-6]|blockquote|pre)>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        text = bleach.clean(text, tags=[], strip=True)
        text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _entry_markdown(entry: dict[str, Any], attachment: str = "") -> str:
    def one_line(value: Any) -> str:
        return str(value or "").replace("\r", " ").replace("\n", " ").strip()

    kind_label = KINDS.get(entry.get("kind", ""), KINDS["other"])[0]
    parts = [
        f"# {one_line(entry.get('title')) or '未命名条目'}",
        "",
        f"- 类型：{kind_label}",
        f"- 领域：{one_line(entry.get('domain')) or '未分类'}",
        f"- 标签：{one_line(entry.get('tags')) or '无'}",
        f"- 来源：{one_line(entry.get('source')) or '未填写'}",
        f"- 创建：{one_line(entry.get('created_at'))}",
        f"- 更新：{one_line(entry.get('updated_at'))}",
    ]
    if attachment:
        parts.append(
            f"- 原始附件：[{one_line(entry.get('original_name')) or '打开附件'}](../{attachment})"
        )
    summary = _plain_export_text(str(entry.get("summary", "")))
    content = _plain_export_text(
        str(entry.get("content", "")),
        str(entry.get("content_format", "plain")),
    )
    if summary:
        parts.extend(["", "## 摘要", "", summary])
    if content:
        parts.extend(["", "## 正文", "", content])
    if not summary and not content:
        parts.extend(["", "> 该条目暂时没有可导出的文本，原始附件仍会随包保存。"])
    return "\n".join(parts).strip() + "\n"


@app.get("/knowledge/export", name="knowledge_export")
def knowledge_export():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = BACKUP_DIR / f"我的科研知识库_{timestamp}.zip"
    with connect() as conn:
        entries = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM entries WHERE status='active' ORDER BY domain,kind,updated_at DESC,id"
            )
        ]
        experiments = [
            dict(row)
            for row in conn.execute("SELECT * FROM experiments ORDER BY experiment_date DESC,id")
        ]
        simulations = [
            dict(row)
            for row in conn.execute("SELECT * FROM simulations ORDER BY updated_at DESC,id")
        ]
        review_sources = [
            dict(row)
            for row in conn.execute(
                "SELECT id,source_type,title,source_text,source_date,created_at "
                "FROM review_sources ORDER BY source_date DESC,id"
            )
        ]
        workspaces = [
            dict(row)
            for row in conn.execute("SELECT * FROM workspaces ORDER BY sort_order,id")
        ]
        research_projects = [
            dict(row)
            for row in conn.execute("SELECT * FROM research_projects ORDER BY updated_at DESC,id")
        ]
        project_workspaces = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_workspaces ORDER BY project_id,is_primary DESC,workspace_id"
            )
        ]
        project_milestones = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_milestones ORDER BY project_id,sort_order,id"
            )
        ]
        project_cases = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_cases ORDER BY project_id,created_at,id"
            )
        ]
        project_updates = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_updates ORDER BY project_id,created_at,id"
            )
        ]
        career_moments = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM career_moments ORDER BY occurred_on DESC,id DESC"
            )
        ]
    exported_at = now_iso()
    manifest = {
        "format": "research-cultivation-knowledge-v5",
        "exported_at": exported_at,
        "counts": {
            "entries": len(entries),
            "experiments": len(experiments),
            "simulations": len(simulations),
            "workspaces": len(workspaces),
            "research_projects": len(research_projects),
            "project_workspaces": len(project_workspaces),
            "project_milestones": len(project_milestones),
            "project_cases": len(project_cases),
            "project_updates": len(project_updates),
            "career_moments": len(career_moments),
            "review_sources": len(review_sources),
        },
    }
    readme = f"""# 我的科研知识库

导出时间：{exported_at}

这个压缩包采用开放、可直接阅读的格式：

- `entries/`：每条知识记录一份 Markdown；
- `attachments/`：知识条目的原始附件；
- `knowledge.json`：完整结构化索引，便于以后用 Python、Excel 或其他软件处理；
- `records/`：课题推进、生涯节点、个人工作区、实验、模拟和复盘关键文本索引；
- `manifest.json`：格式版本与数量校验。

本包不包含灵石、游戏资产、API 密钥、联机 Token 或软件程序。需要完整恢复整个系统时，请在网站“设置与备份”中下载完整备份。
"""
    structured_entries: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("README.md", readme)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for entry in entries:
            attachment_rel = ""
            stored_name = Path(str(entry.get("file_path", ""))).name
            source = UPLOAD_DIR / stored_name if stored_name else None
            if source and source.is_file() and source.parent.resolve() == UPLOAD_DIR.resolve():
                attachment_name = (
                    f"{int(entry['id']):06d}_{secure_filename(str(entry.get('original_name') or source.name))}"
                )
                attachment_rel = (Path("attachments") / attachment_name).as_posix()
                zf.write(source, attachment_rel)
            markdown_name = (
                f"{int(entry['id']):06d}_{secure_filename(str(entry.get('title') or '未命名'))}.md"
            )
            zf.writestr(
                (Path("entries") / markdown_name).as_posix(),
                _entry_markdown(entry, attachment_rel),
            )
            item = dict(entry)
            item["attachment"] = attachment_rel
            structured_entries.append(item)
        zf.writestr(
            "knowledge.json",
            json.dumps(
                {
                    "format": manifest["format"],
                    "exported_at": exported_at,
                    "entries": structured_entries,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        zf.writestr(
            "records/workspaces.json",
            json.dumps(workspaces, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/research_projects.json",
            json.dumps(research_projects, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/project_workspaces.json",
            json.dumps(project_workspaces, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/project_milestones.json",
            json.dumps(project_milestones, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/project_cases.json",
            json.dumps(project_cases, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/project_updates.json",
            json.dumps(project_updates, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/career_moments.json",
            json.dumps(career_moments, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/experiments.json",
            json.dumps(experiments, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/simulations.json",
            json.dumps(simulations, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "records/review_sources.json",
            json.dumps(review_sources, ensure_ascii=False, indent=2),
        )
    log_activity("knowledge_export", 0, f"导出科研知识库：{len(entries)} 条知识记录")
    return FileResponse(archive, media_type="application/zip", filename=archive.name)


@app.get("/backup", name="backup")
def backup(request: Request):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = BACKUP_DIR / f"research_os_backup_{timestamp}"
    stage.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        source_conn = connect()
        destination_conn = sqlite3.connect(stage / DB_PATH.name)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
            source_conn.close()
    storage_stage = stage / "storage"
    storage_stage.mkdir(exist_ok=True)
    for name, source in (("uploads", UPLOAD_DIR), ("simulations", SIMULATION_DIR), ("research_foundation", FOUNDATION_DIR), ("deliveries", DELIVERY_DIR), ("note_images", NOTE_IMAGE_DIR), ("profile", PROFILE_DIR)):
        target = storage_stage / name
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".gitkeep"))
        else:
            target.mkdir(parents=True, exist_ok=True)
    if USER_CONFIG_DIR.exists():
        shutil.copytree(
            USER_CONFIG_DIR,
            stage / "user_config",
            dirs_exist_ok=True,
        )
    manifest = {"version": get_setting("portable_version", APP_VERSION), "created_at": now_iso(), "database": DB_PATH.name}
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(stage), "zip", root_dir=stage)
    shutil.rmtree(stage, ignore_errors=True)
    log_activity("backup", 5, "完成本地知识库备份")
    return FileResponse(archive, media_type="application/zip", filename=Path(archive).name)


@app.post("/feedback/save", name="feedback_save")
def feedback_save(request: Request, kind: str = Form("bug"), content: str = Form(""), contact: str = Form("")):
    from core.database import connect as _connect
    from services.sync_backend import build_sync_backend

    content = " ".join(content.split())
    if len(content) < 10:
        flash(request, "反馈内容至少 10 个字。", "error")
        return redirect("settings", request)
    if len(content) > 4000:
        flash(request, "反馈内容过长（不超过 4000 字）。", "error")
        return redirect("settings", request)
    kind = kind if kind in {"bug", "idea", "other"} else "bug"
    contact = contact.strip()[:160]
    now = now_iso()
    hub_url = get_setting("hub_url", "").strip()
    hub_token = get_setting("hub_api_token", "").strip()
    hub_provider = get_setting("sync_provider", "disabled")
    synced = 0
    note = "已保存在本机。"
    if hub_provider == "legacy_hub" and hub_url and hub_token:
        try:
            backend = build_sync_backend(hub_provider, hub_url, hub_token)
            backend.feedback(kind, content, contact, timeout=6.0)
            synced = 1
            note = "已提交到同行会中心，感谢反馈！"
        except Exception as exc:
            note = f"已保存在本机（中心暂不可达：{exc}）。可稍后在联机通道重试同步。"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_feedback(kind,content,contact,hub_synced,created_at) VALUES (?,?,?,?,?)",
            (kind, content, contact, synced, now),
        )
        conn.commit()
    flash(request, note, "success" if synced else "info")
    return redirect("settings_page", request)
