from __future__ import annotations

from fastapi import APIRouter

from web.runtime import (
    Any,
    BACKUP_DIR,
    FOUNDATION_DIR,
    File,
    FileResponse,
    Form,
    HTMLResponse,
    HTTPException,
    Path,
    RedirectResponse,
    Request,
    UploadFile,
    connect,
    context,
    datetime,
    flash,
    get_setting,
    json,
    mimetypes,
    now_iso,
    re,
    redirect,
    secure_filename,
    shutil,
    sqlite3,
    templates,
    uuid,
    zipfile,
)
from services.file_storage import MAX_UPLOAD_BYTES, save_upload_limited

app = APIRouter()

def _safe_relative_path(value: str) -> Path:
    """Return a portable, traversal-safe relative path while preserving folders."""
    raw = (value or "").replace("\\", "/").strip("/")
    parts: list[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        parts.append(secure_filename(part))
    return Path(*parts) if parts else Path("file")


def _folder_stats(path: Path) -> tuple[int, int]:
    count = 0
    total = 0
    if path.exists():
        for item in path.rglob("*"):
            if item.is_file():
                count += 1
                total += item.stat().st_size
    return count, total


def _foundation_tracks(conn) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM research_tracks WHERE active=1 ORDER BY sort_order,id"):
        item = dict(row)
        item["tasks"] = [dict(x) for x in conn.execute(
            "SELECT * FROM research_plan_items WHERE track_id=? ORDER BY sort_order,id", (row["id"],)
        )]
        item["folders"] = [dict(x) for x in conn.execute(
            "SELECT * FROM research_folders WHERE track_id=? ORDER BY updated_at DESC", (row["id"],)
        )]
        total = len(item["tasks"])
        completed = sum(1 for x in item["tasks"] if x["status"] == "done")
        item["progress"] = round(completed / total * 100) if total else 0
        tracks.append(item)
    return tracks


def _parse_foundation_text(text: str) -> list[dict[str, Any]]:
    """Parse a forgiving Markdown-like master plan into research tracks."""
    tracks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    field_map = {
        "目标": "objective", "长期目标": "objective", "主目标": "objective",
        "当前阶段": "current_stage", "阶段": "current_stage",
        "下一步": "next_focus", "近期重点": "next_focus", "当前重点": "next_focus",
        "备注": "notes", "说明": "notes",
    }
    status_map = {"x": "done", "✓": "done", "完成": "done", "进行中": "active", "推进": "active", "暂停": "paused"}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^(?:#{1,4}\s*|【)([^】#]+?)(?:】)?$", line)
        if heading:
            name = heading.group(1).strip()
            if name and name not in {"科研底座", "研究计划", "总计划"}:
                current = {"name": name, "icon": "◇", "objective": "", "current_stage": "", "next_focus": "", "notes": "", "tasks": []}
                tracks.append(current)
            continue
        if current is None:
            section = re.match(r"^([^：:]{2,30})[：:]\s*$", line)
            if section:
                current = {"name": section.group(1).strip(), "icon": "◇", "objective": "", "current_stage": "", "next_focus": "", "notes": "", "tasks": []}
                tracks.append(current)
                continue
            else:
                continue
        field = re.match(r"^([^：:]{1,12})[：:]\s*(.+)$", line)
        if field and field.group(1).strip() in field_map:
            current[field_map[field.group(1).strip()]] = field.group(2).strip()
            continue
        icon_match = re.match(r"^(?:图标|ICON)[：:]\s*(.+)$", line, re.I)
        if icon_match:
            current["icon"] = icon_match.group(1).strip()[:4]
            continue
        task_match = re.match(r"^[-*+]\s*(?:\[([^\]]*)\])?\s*(.+)$", line)
        if task_match:
            marker = (task_match.group(1) or "").strip().lower()
            body = task_match.group(2).strip()
            parts = [x.strip() for x in re.split(r"\s*\|\s*", body) if x.strip()]
            title = parts[0]
            task = {"title": title, "description": "", "deliverable": "", "status": status_map.get(marker, "planned"), "priority": "normal", "due_date": ""}
            for part in parts[1:]:
                kv = re.match(r"^([^：:]+)[：:]\s*(.*)$", part)
                if not kv:
                    task["description"] = (task["description"] + " " + part).strip()
                    continue
                key, value = kv.group(1).strip(), kv.group(2).strip()
                if key in {"交付", "交付物", "证据"}:
                    task["deliverable"] = value
                elif key in {"说明", "描述"}:
                    task["description"] = value
                elif key in {"状态"}:
                    task["status"] = {
                        "完成": "done",
                        "进行中": "active",
                        "暂停": "paused",
                        "计划": "planned",
                    }.get(value, value)
                elif key in {"优先", "优先级"}:
                    task["priority"] = {
                        "高": "high",
                        "中": "normal",
                        "低": "low",
                    }.get(value, value)
                elif key in {"截止", "日期"}:
                    task["due_date"] = (
                        value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else ""
                    )
            current["tasks"].append(task)
    return tracks


@app.get("/foundation", response_class=HTMLResponse, name="foundation_page")
def foundation_page(request: Request, edit_track: int | None = None):
    # v3 keeps the long-term foundation tools behind the plan entry point.
    # The clean schema still owns these tables because they are native v3 data.
    return RedirectResponse(request.url_for("plans_page"), status_code=303)
    # The renderer remains available to this route module's native v3 actions.
    with connect() as conn:
        tracks = _foundation_tracks(conn)
        unassigned_folders = [dict(x) for x in conn.execute(
            "SELECT * FROM research_folders WHERE track_id IS NULL ORDER BY updated_at DESC"
        )]
        totals = dict(conn.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) done, COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) active FROM research_plan_items"
        ).fetchone())
    return templates.TemplateResponse(
        request=request,
        name="foundation.html",
        context=context(
            request, "foundation", tracks=tracks, unassigned_folders=unassigned_folders,
            master_text=get_setting("foundation_master_text", ""), totals=totals, edit_track=edit_track,
        ),
    )


@app.post("/foundation/sync", name="foundation_sync")
def foundation_sync(request: Request, master_text: str = Form(...)):
    parsed = _parse_foundation_text(master_text)
    if not parsed:
        flash(request, "没有识别到学科标题。请使用“# 学科名”开始每条路线。", "error")
        return redirect("foundation_page", request)
    ts = now_iso()
    with connect() as conn:
        conn.execute("UPDATE research_tracks SET sort_order=sort_order+?", (len(parsed),))
        for order, track in enumerate(parsed):
            row = conn.execute("SELECT id,icon FROM research_tracks WHERE name=?", (track["name"],)).fetchone()
            if row:
                track_id = row["id"]
                icon = track["icon"] if track["icon"] != "◇" else row["icon"]
                conn.execute(
                    "UPDATE research_tracks SET icon=?,objective=?,current_stage=?,next_focus=?,notes=?,sort_order=?,active=1,updated_at=? WHERE id=?",
                    (icon, track["objective"], track["current_stage"], track["next_focus"], track["notes"], order, ts, track_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO research_tracks(name,icon,objective,current_stage,next_focus,notes,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (track["name"], track["icon"], track["objective"], track["current_stage"], track["next_focus"], track["notes"], order, ts, ts),
                )
                track_id = int(cur.lastrowid)
            if track["tasks"]:
                conn.execute("DELETE FROM research_plan_items WHERE track_id=?", (track_id,))
                for item_order, task in enumerate(track["tasks"]):
                    conn.execute(
                        "INSERT INTO research_plan_items(track_id,title,description,deliverable,status,priority,due_date,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (track_id, task["title"], task["description"], task["deliverable"], task["status"], task["priority"], task["due_date"] or None, item_order, ts, ts),
                    )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('foundation_master_text',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (master_text.strip(),),
        )
        conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("foundation_sync", 18, f"同步科研底座：{len(parsed)}条学科路线", ts))
        conn.commit()
    flash(request, f"已同步 {len(parsed)} 条学科路线。未写日期的任务会长期保留。")
    return redirect("foundation_page", request)


@app.post("/foundation/tracks/new", name="foundation_track_new")
def foundation_track_new(request: Request, name: str = Form(...), icon: str = Form("◇")):
    name = name.strip()
    if not name:
        flash(request, "学科名称不能为空。", "error")
        return redirect("foundation_page", request)
    ts = now_iso()
    try:
        with connect() as conn:
            order = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 n FROM research_tracks").fetchone()["n"]
            conn.execute("INSERT INTO research_tracks(name,icon,sort_order,created_at,updated_at) VALUES (?,?,?,?,?)", (name, icon.strip()[:4] or "◇", order, ts, ts))
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("foundation_track", 8, f"建立学科路线：{name}", ts))
            conn.commit()
    except sqlite3.IntegrityError:
        flash(request, "同名学科路线已经存在。", "error")
        return redirect("foundation_page", request)
    flash(request, "学科路线已建立。")
    return redirect("foundation_page", request)


@app.post("/foundation/tracks/{track_id}/save", name="foundation_track_save")
def foundation_track_save(
    request: Request, track_id: int, name: str = Form(...), icon: str = Form("◇"),
    objective: str = Form(""), current_stage: str = Form(""), next_focus: str = Form(""), notes: str = Form(""),
):
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE research_tracks SET name=?,icon=?,objective=?,current_stage=?,next_focus=?,notes=?,updated_at=? WHERE id=?",
                (name.strip(), icon.strip()[:4] or "◇", objective.strip(), current_stage.strip(), next_focus.strip(), notes.strip(), now_iso(), track_id),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        flash(request, "同名学科路线已经存在，请换一个名称。", "error")
        return RedirectResponse(url=f"{request.url_for('foundation_page')}?edit_track={track_id}", status_code=303)
    flash(request, "这条学科路线已更新。")
    return redirect("foundation_track_page", request, track_id=track_id)


@app.post("/foundation/tracks/{track_id}/delete", name="foundation_track_delete")
def foundation_track_delete(request: Request, track_id: int):
    with connect() as conn:
        row = conn.execute("SELECT name FROM research_tracks WHERE id=?", (track_id,)).fetchone()
        conn.execute("DELETE FROM research_tracks WHERE id=?", (track_id,))
        conn.commit()
    flash(request, f"已删除学科路线：{row['name'] if row else track_id}。")
    return redirect("foundation_page", request)


@app.post("/foundation/tracks/{track_id}/tasks/new", name="foundation_task_new")
def foundation_task_new(
    request: Request, track_id: int, title: str = Form(...), description: str = Form(""),
    deliverable: str = Form(""), priority: str = Form("normal"), due_date: str = Form(""),
):
    ts = now_iso()
    with connect() as conn:
        order = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 n FROM research_plan_items WHERE track_id=?", (track_id,)).fetchone()["n"]
        conn.execute(
            "INSERT INTO research_plan_items(track_id,title,description,deliverable,priority,due_date,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (track_id, title.strip(), description.strip(), deliverable.strip(), priority, due_date or None, order, ts, ts),
        )
        conn.commit()
    flash(request, "计划项已加入；截止日期可以留空，作为长期任务。")
    return redirect("foundation_track_page", request, track_id=track_id)


@app.post("/foundation/tasks/{task_id}/save", name="foundation_task_save")
def foundation_task_save(
    request: Request, task_id: int, title: str = Form(...), description: str = Form(""),
    deliverable: str = Form(""), status: str = Form("planned"), priority: str = Form("normal"), due_date: str = Form(""),
):
    with connect() as conn:
        row = conn.execute("SELECT track_id FROM research_plan_items WHERE id=?", (task_id,)).fetchone()
        conn.execute(
            "UPDATE research_plan_items SET title=?,description=?,deliverable=?,status=?,priority=?,due_date=?,updated_at=? WHERE id=?",
            (title.strip(), description.strip(), deliverable.strip(), status, priority, due_date or None, now_iso(), task_id),
        )
        conn.commit()
    flash(request, "计划项已修改。")
    return redirect("foundation_track_page", request, track_id=row["track_id"] if row else 1)


@app.post("/foundation/tasks/{task_id}/toggle", name="foundation_task_toggle")
def foundation_task_toggle(request: Request, task_id: int):
    with connect() as conn:
        row = conn.execute("SELECT status,title,track_id FROM research_plan_items WHERE id=?", (task_id,)).fetchone()
        if row:
            new_status = "done" if row["status"] != "done" else "active"
            conn.execute("UPDATE research_plan_items SET status=?,updated_at=? WHERE id=?", (new_status, now_iso(), task_id))
            if new_status == "done":
                conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("foundation_task_done", 12, f"完成底座计划：{row['title']}", now_iso()))
            conn.commit()
    return redirect("foundation_track_page", request, track_id=row["track_id"] if row else 1)


@app.post("/foundation/tasks/{task_id}/delete", name="foundation_task_delete")
def foundation_task_delete(request: Request, task_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM research_plan_items WHERE id=?", (task_id,))
        conn.commit()
    flash(request, "计划项已删除。")
    return redirect("foundation_page", request)


@app.post("/foundation/folders/upload", name="foundation_folder_upload")
def foundation_folder_upload(
    request: Request, folder_name: str = Form(""), description: str = Form(""), track_id: str = Form(""),
    relative_paths: str = Form("[]"), files: list[UploadFile] = File(default=[]),
):
    uploads = [item for item in files if item.filename]
    if not uploads:
        flash(request, "请选择一个包含文件的文件夹。", "error")
        return redirect("foundation_page", request)
    try:
        rel_paths = json.loads(relative_paths or "[]")
    except json.JSONDecodeError:
        rel_paths = []
    raw_root = ""
    for candidate in rel_paths:
        if candidate:
            raw_root = str(candidate).replace("\\", "/").split("/")[0]
            break
    display_name = folder_name.strip() or raw_root or f"科研交付_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    storage_key = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    root = FOUNDATION_DIR / storage_key
    root.mkdir(parents=True, exist_ok=True)
    ts = now_iso()
    track_value = int(track_id) if track_id.strip().isdigit() else None
    saved: list[tuple[Path, str, str, int]] = []
    try:
        used_bytes = 0
        for index, upload in enumerate(uploads):
            rel_value = rel_paths[index] if index < len(rel_paths) else upload.filename or f"file_{index}"
            rel = _safe_relative_path(str(rel_value))
            # Drop the selected root folder name because the archive already has its own root.
            if len(rel.parts) > 1 and raw_root and rel.parts[0] == secure_filename(raw_root):
                rel = Path(*rel.parts[1:])
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            size = save_upload_limited(
                upload,
                target,
                remaining_bytes=MAX_UPLOAD_BYTES - used_bytes,
            )
            used_bytes += size
            saved.append((target, str(rel).replace("\\", "/"), upload.content_type or mimetypes.guess_type(target.name)[0] or "", size))
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO research_folders(track_id,name,description,storage_key,file_count,total_size,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (track_value, display_name, description.strip(), storage_key, len(saved), sum(x[3] for x in saved), ts, ts),
            )
            folder_id = int(cur.lastrowid)
            for target, rel, mime, size in saved:
                conn.execute(
                    "INSERT INTO research_folder_files(folder_id,relative_path,stored_path,original_name,mime_type,file_size,created_at) VALUES (?,?,?,?,?,?,?)",
                    (folder_id, rel, rel, target.name, mime, size, ts),
                )
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("foundation_folder", min(35, 10 + len(saved)), f"归档科研交付文件夹：{display_name}", ts))
            conn.commit()
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        flash(request, f"文件夹归档失败：{exc}", "error")
        return redirect("foundation_page", request)
    flash(request, f"文件夹“{display_name}”已独立归档，共 {len(saved)} 个文件。")
    return redirect("foundation_page", request)


@app.get("/foundation/folders/{folder_id}", response_class=HTMLResponse, name="foundation_folder_view")
def foundation_folder_view(request: Request, folder_id: int):
    with connect() as conn:
        folder = conn.execute("SELECT f.*,t.name track_name FROM research_folders f LEFT JOIN research_tracks t ON t.id=f.track_id WHERE f.id=?", (folder_id,)).fetchone()
        files = [dict(x) for x in conn.execute("SELECT * FROM research_folder_files WHERE folder_id=? ORDER BY relative_path", (folder_id,))]
    if not folder:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request=request, name="foundation_folder.html", context=context(request, "foundation", folder=dict(folder), files=files))


@app.get("/foundation/folders/{folder_id}/files/{file_id}", name="foundation_folder_file")
def foundation_folder_file(folder_id: int, file_id: int):
    with connect() as conn:
        row = conn.execute("SELECT f.storage_key,ff.stored_path,ff.original_name,ff.mime_type FROM research_folder_files ff JOIN research_folders f ON f.id=ff.folder_id WHERE ff.id=? AND ff.folder_id=?", (file_id, folder_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    root = (FOUNDATION_DIR / row["storage_key"]).resolve()
    path = (root / row["stored_path"]).resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, filename=row["original_name"], media_type=row["mime_type"] or None)


@app.get("/foundation/folders/{folder_id}/download", name="foundation_folder_download")
def foundation_folder_download(folder_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM research_folders WHERE id=?", (folder_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    root = FOUNDATION_DIR / row["storage_key"]
    archive = BACKUP_DIR / f"{secure_filename(row['name'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root))
    return FileResponse(archive, filename=archive.name, media_type="application/zip")


@app.post("/foundation/folders/{folder_id}/delete", name="foundation_folder_delete")
def foundation_folder_delete(request: Request, folder_id: int):
    with connect() as conn:
        row = conn.execute("SELECT storage_key,name FROM research_folders WHERE id=?", (folder_id,)).fetchone()
        conn.execute("DELETE FROM research_folders WHERE id=?", (folder_id,))
        conn.commit()
    if row:
        shutil.rmtree(FOUNDATION_DIR / row["storage_key"], ignore_errors=True)
    flash(request, f"文件夹“{row['name'] if row else folder_id}”已删除。")
    return redirect("foundation_page", request)
