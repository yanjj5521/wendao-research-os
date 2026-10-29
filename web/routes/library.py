from __future__ import annotations

from fastapi import APIRouter

from web.runtime import (
    Any,
    File,
    FileResponse,
    Form,
    HTMLResponse,
    HTTPException,
    JSONResponse,
    KINDS,
    MAX_UPLOAD_BYTES,
    NOTE_IMAGE_DIR,
    Path,
    RedirectResponse,
    Request,
    UPLOAD_DIR,
    UploadFile,
    connect,
    context,
    datetime,
    entry_dict,
    extract_file,
    fixed_cultivation_xp,
    flash,
    infer_kind,
    json,
    mimetypes,
    now_iso,
    re,
    redirect,
    sanitize_rich_content,
    secure_filename,
    templates,
    uuid,
    xp_for_kind,
)
from services.file_storage import save_upload_limited

app = APIRouter()

@app.get("/library", response_class=HTMLResponse, name="library")
def library(request: Request, kind: str = "", domain: str = "", favorite: str = "", workspace: int = 0):
    sql = "SELECT * FROM entries WHERE status='active'"
    params: list[Any] = []
    if workspace:
        sql += " AND workspace_id=?"
        params.append(workspace)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if domain:
        sql += " AND domain=?"
        params.append(domain)
    if favorite == "1":
        sql += " AND favorite=1"
    sql += " ORDER BY favorite DESC, updated_at DESC LIMIT 300"
    with connect() as conn:
        entries = [entry_dict(row) for row in conn.execute(sql, params)]
        selected_workspace = conn.execute(
            "SELECT id,name,icon FROM workspaces WHERE id=?", (workspace,)
        ).fetchone() if workspace else None
    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context=context(
            request,
            "library",
            entries=entries,
            selected_kind=kind,
            selected_domain=domain,
            favorite=favorite,
            selected_workspace=dict(selected_workspace) if selected_workspace else None,
            active_workspace_id=workspace or None,
        ),
    )


@app.get("/datasets", response_class=HTMLResponse, name="datasets_page")
def datasets_page(request: Request, workspace: int = 0):
    with connect() as conn:
        selected = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace,)).fetchone() if workspace else None
        if selected:
            rows = conn.execute(
                "SELECT * FROM entries WHERE kind='dataset' AND workspace_id=? ORDER BY updated_at DESC LIMIT 300",
                (workspace,),
            )
        else:
            rows = conn.execute("SELECT * FROM entries WHERE kind='dataset' ORDER BY updated_at DESC LIMIT 300")
        entries = [entry_dict(row) for row in rows]
    return templates.TemplateResponse(
        request=request,
        name="datasets.html",
        context=context(
            request,
            "datasets",
            entries=entries,
            workspace=dict(selected) if selected else None,
            active_workspace_id=workspace or None,
        ),
    )


@app.get("/upload", response_class=HTMLResponse, name="upload")
def upload_get(request: Request, workspace_id: int = 0):
    with connect() as conn:
        workspaces = [
            dict(row)
            for row in conn.execute("SELECT id,name,module FROM workspaces WHERE active=1 ORDER BY sort_order,id")
        ]
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context=context(request, "upload", workspaces=workspaces, selected_workspace_id=workspace_id),
    )


@app.post("/upload", name="upload_post")
def upload_post(
    request: Request,
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    kind: str = Form("auto"),
    domain: str = Form("未分类"),
    tags: str = Form(""),
    summary: str = Form(""),
    source: str = Form(""),
    workspace_id: str = Form(""),
):
    files = [item for item in files if item.filename]
    if not files:
        flash(request, "请选择至少一个文件。", "error")
        return redirect("upload", request)
    created_ids: list[int] = []
    saved_paths: list[Path] = []
    with connect() as conn:
        try:
            workspace_value = int(workspace_id) if workspace_id.isdigit() else None
            if workspace_value and not conn.execute(
                "SELECT id FROM workspaces WHERE id=? AND active=1", (workspace_value,)
            ).fetchone():
                workspace_value = None
            for upload in files:
                original_name = upload.filename or "unnamed"
                safe_name = secure_filename(original_name) or f"file_{uuid.uuid4().hex}"
                stored_name = f"{uuid.uuid4().hex}_{safe_name}"
                stored_path = UPLOAD_DIR / stored_name
                save_upload_limited(upload, stored_path, max_bytes=MAX_UPLOAD_BYTES)
                saved_paths.append(stored_path)
                extracted = extract_file(stored_path)
                inferred_kind = infer_kind(original_name, kind)
                entry_title = title.strip() if title.strip() and len(files) == 1 else Path(original_name).stem
                item_summary = summary.strip()
                if extracted.get("error"):
                    item_summary = (item_summary + "\n" + extracted["error"]).strip()
                ts = now_iso()
                cursor = conn.execute(
                    """
                    INSERT INTO entries(
                        title, kind, domain, tags, summary, content, file_path, original_name,
                        mime_type, file_size, dataset_rows, dataset_columns, dataset_schema,
                        dataset_preview, source, created_at, updated_at, extract_status, indexed_at
                        ,workspace_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_title,
                        inferred_kind,
                        domain.strip() or "未分类",
                        tags.strip(),
                        item_summary,
                        extracted.get("content", ""),
                        stored_name,
                        original_name,
                        extracted.get("mime_type", upload.content_type or "application/octet-stream"),
                        stored_path.stat().st_size,
                        extracted.get("rows"),
                        extracted.get("columns"),
                        json.dumps(extracted.get("schema", []), ensure_ascii=False),
                        json.dumps(extracted.get("preview", []), ensure_ascii=False, default=str),
                        source.strip(),
                        ts,
                        ts,
                        "ok" if extracted.get("content") else ("error" if extracted.get("error") else "no_text"),
                        ts,
                        workspace_value,
                    ),
                )
                entry_id = int(cursor.lastrowid)
                created_ids.append(entry_id)
                conn.execute(
                    "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("upload", entry_id, xp_for_kind(inferred_kind), f"收录：{entry_title}", ts),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            for path in saved_paths:
                path.unlink(missing_ok=True)
            flash(request, f"上传失败：{exc}", "error")
            return redirect("upload", request)
    flash(request, f"成功收录 {len(created_ids)} 项科研资料，修为已增长。")
    return redirect("entry_view", request, entry_id=str(created_ids[-1]))


@app.get("/notes/new", response_class=HTMLResponse, name="note_new")
def note_new_get(request: Request, workspace_id: int = 0):
    with connect() as conn:
        workspaces = [
            dict(row)
            for row in conn.execute("SELECT id,name FROM workspaces WHERE active=1 ORDER BY sort_order,id")
        ]
    return templates.TemplateResponse(
        request=request,
        name="editor.html",
        context=context(
            request,
            "note_new",
            entry=None,
            workspaces=workspaces,
            selected_workspace_id=workspace_id,
        ),
    )


@app.post("/notes/new", name="note_new_post")
def note_new_post(
    request: Request,
    title: str = Form(...),
    kind: str = Form("note"),
    domain: str = Form("未分类"),
    tags: str = Form(""),
    summary: str = Form(""),
    content: str = Form(""),
    source: str = Form(""),
    workspace_id: str = Form(""),
):
    title = title.strip()
    if not title:
        flash(request, "标题不能为空。", "error")
        return redirect("note_new", request)
    if kind not in KINDS:
        kind = "note"
    ts = now_iso()
    with connect() as conn:
        workspace_value = int(workspace_id) if workspace_id.isdigit() else None
        cursor = conn.execute(
            """
            INSERT INTO entries(title, kind, domain, tags, summary, content, source, workspace_id, created_at, updated_at, content_format)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'html')
            """,
            (
                title,
                kind,
                domain.strip() or "未分类",
                tags.strip(),
                summary.strip(),
                sanitize_rich_content(content),
                source.strip(),
                workspace_value,
                ts,
                ts,
            ),
        )
        entry_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            ("create", entry_id, xp_for_kind(kind), f"新建：{title}", ts),
        )
        conn.commit()
    flash(request, "记录已保存，并转化为你的科研资产。")
    return redirect("entry_view", request, entry_id=str(entry_id))


@app.post("/notes/paste-image", name="note_paste_image")
def note_paste_image(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        return JSONResponse({"ok": False, "error": "只能粘贴图片"}, status_code=400)
    suffix = Path(file.filename or "pasted.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        suffix = ".png"
    name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}{suffix}"
    target = NOTE_IMAGE_DIR / name
    try:
        save_upload_limited(file, target, max_bytes=25 * 1024 * 1024)
    except ValueError:
        return JSONResponse({"ok": False, "error": "图片不能超过25MB"}, status_code=400)
    with connect() as conn:
        row = conn.execute("SELECT unlocked FROM easter_eggs WHERE egg_key='image_note'").fetchone()
        if row and not row["unlocked"]:
            conn.execute("UPDATE easter_eggs SET unlocked=1,discovered_at=? WHERE egg_key='image_note'", (now_iso(),))
            conn.commit()
    return {"ok": True, "url": f"/media/note-images/{name}"}


@app.get("/entry/{entry_id}", response_class=HTMLResponse, name="entry_view")
def entry_view(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        entry = entry_dict(row)
        related = [
            entry_dict(r)
            for r in conn.execute(
                "SELECT * FROM entries WHERE id != ? AND (domain=? OR kind=?) ORDER BY updated_at DESC LIMIT 6",
                (entry_id, entry["domain"], entry["kind"]),
            )
        ]
    return templates.TemplateResponse(
        request=request,
        name="entry.html",
        context=context(request, "entry", entry=entry, related=related),
    )


@app.get("/entry/{entry_id}/edit", response_class=HTMLResponse, name="entry_edit")
def entry_edit_get(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        workspaces = [
            dict(item)
            for item in conn.execute("SELECT id,name FROM workspaces WHERE active=1 ORDER BY sort_order,id")
        ]
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="editor.html",
        context=context(
            request,
            "entry",
            entry=entry_dict(row),
            workspaces=workspaces,
            selected_workspace_id=int(row["workspace_id"] or 0),
        ),
    )


@app.post("/entry/{entry_id}/edit", name="entry_edit_post")
def entry_edit_post(
    request: Request,
    entry_id: int,
    title: str = Form(...),
    kind: str = Form("note"),
    domain: str = Form("未分类"),
    tags: str = Form(""),
    summary: str = Form(""),
    content: str = Form(""),
    source: str = Form(""),
    workspace_id: str = Form(""),
    file: UploadFile | None = File(None),
):
    with connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        entry = entry_dict(row)
        title = title.strip()
        if not title:
            flash(request, "标题不能为空。", "error")
            return redirect("entry_edit", request, entry_id=str(entry_id))
        fields: dict[str, Any] = {
            "title": title,
            "kind": kind if kind in KINDS else entry["kind"],
            "domain": domain.strip() or "未分类",
            "tags": tags.strip(),
            "summary": summary.strip(),
            "content": sanitize_rich_content(content),
            "content_format": "html",
            "source": source.strip(),
            "workspace_id": int(workspace_id) if workspace_id.isdigit() else None,
            "updated_at": now_iso(),
        }
        old_path: Path | None = None
        new_path: Path | None = None
        try:
            if file and file.filename:
                original_name = file.filename
                safe_name = secure_filename(original_name) or f"file_{uuid.uuid4().hex}"
                stored_name = f"{uuid.uuid4().hex}_{safe_name}"
                new_path = UPLOAD_DIR / stored_name
                save_upload_limited(file, new_path, max_bytes=MAX_UPLOAD_BYTES)
                extracted = extract_file(new_path)
                old_path = UPLOAD_DIR / entry["file_path"] if entry.get("file_path") else None
                fields.update(
                    {
                        "file_path": stored_name,
                        "original_name": original_name,
                        "mime_type": extracted.get("mime_type", file.content_type or "application/octet-stream"),
                        "file_size": new_path.stat().st_size,
                        "content": extracted.get("content", "") or sanitize_rich_content(content),
                        "content_format": "plain" if extracted.get("content") else "html",
                        "dataset_rows": extracted.get("rows"),
                        "dataset_columns": extracted.get("columns"),
                        "dataset_schema": json.dumps(extracted.get("schema", []), ensure_ascii=False),
                        "dataset_preview": json.dumps(extracted.get("preview", []), ensure_ascii=False, default=str),
                    }
                )
            assignments = ", ".join(f"{key}=?" for key in fields)
            conn.execute(f"UPDATE entries SET {assignments} WHERE id=?", [*fields.values(), entry_id])
            conn.execute(
                "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                ("edit", entry_id, 4, f"完善：{title}", now_iso()),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if new_path:
                new_path.unlink(missing_ok=True)
            flash(request, f"更新失败：{exc}", "error")
            return redirect("entry_edit", request, entry_id=str(entry_id))
    if old_path and old_path.exists():
        old_path.unlink(missing_ok=True)
    flash(request, "条目已更新，知识结构更加完整。")
    return redirect("entry_view", request, entry_id=str(entry_id))


@app.post("/entry/{entry_id}/delete", name="entry_delete")
def entry_delete(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT title, file_path FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        conn.execute(
            "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?, NULL, ?, ?, ?)",
            ("delete", 0, f"删除：{row['title']}", now_iso()),
        )
        conn.commit()
    if row["file_path"]:
        (UPLOAD_DIR / row["file_path"]).unlink(missing_ok=True)
    flash(request, "条目已删除。")
    return redirect("library", request)


@app.post("/entry/{entry_id}/favorite", name="entry_favorite")
def entry_favorite(request: Request, entry_id: int):
    with connect() as conn:
        row = conn.execute("SELECT favorite FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        conn.execute("UPDATE entries SET favorite=?, updated_at=? WHERE id=?", (0 if row["favorite"] else 1, now_iso(), entry_id))
        conn.commit()
    referer = request.headers.get("referer")
    return RedirectResponse(url=referer or str(request.url_for("entry_view", entry_id=entry_id)), status_code=303)


@app.get("/search", response_class=HTMLResponse, name="search")
def search(request: Request, q: str = "", kind: str = "", domain: str = ""):
    q = q.strip()
    entries: list[dict[str, Any]] = []
    used_mode = "关键词模糊检索"
    with connect() as conn:
        if q:
            terms = [term for term in re.split(r"\s+", q) if term]
            fts_terms = [term.replace('"', "") for term in terms if term.replace('"', "")]
            if fts_terms:
                try:
                    match = " AND ".join(f'"{term}"*' for term in fts_terms)
                    sql = (
                        "SELECT e.*, bm25(entries_fts) AS rank FROM entries_fts "
                        "JOIN entries e ON e.id=entries_fts.rowid WHERE entries_fts MATCH ?"
                    )
                    params: list[Any] = [match]
                    if kind:
                        sql += " AND e.kind=?"
                        params.append(kind)
                    if domain:
                        sql += " AND e.domain=?"
                        params.append(domain)
                    sql += " ORDER BY rank LIMIT 100"
                    entries = [entry_dict(row) for row in conn.execute(sql, params)]
                    if entries:
                        used_mode = "SQLite FTS5 全文检索"
                except Exception:
                    entries = []
            if not entries:
                conditions = []
                params = []
                searchable = "lower(title || ' ' || summary || ' ' || content || ' ' || tags || ' ' || domain || ' ' || source)"
                for term in terms:
                    conditions.append(f"{searchable} LIKE ?")
                    params.append(f"%{term.lower()}%")
                sql = "SELECT * FROM entries WHERE " + " AND ".join(conditions or ["1=1"])
                if kind:
                    sql += " AND kind=?"
                    params.append(kind)
                if domain:
                    sql += " AND domain=?"
                    params.append(domain)
                sql += " ORDER BY favorite DESC, updated_at DESC LIMIT 100"
                entries = [entry_dict(row) for row in conn.execute(sql, params)]
        else:
            sql = "SELECT * FROM entries WHERE 1=1"
            params = []
            if kind:
                sql += " AND kind=?"
                params.append(kind)
            if domain:
                sql += " AND domain=?"
                params.append(domain)
            sql += " ORDER BY updated_at DESC LIMIT 100"
            entries = [entry_dict(row) for row in conn.execute(sql, params)]
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context=context(
            request,
            "search",
            entries=entries,
            q=q,
            selected_kind=kind,
            selected_domain=domain,
            used_mode=used_mode,
        ),
    )


@app.get("/files/{entry_id}", name="entry_file")
def entry_file(request: Request, entry_id: int, inline: int = 0):
    with connect() as conn:
        row = conn.execute("SELECT file_path, original_name, mime_type FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not row or not row["file_path"]:
        raise HTTPException(status_code=404)
    path = UPLOAD_DIR / row["file_path"]
    if not path.exists():
        raise HTTPException(status_code=404)
    safe_inline = (row["mime_type"] or "").startswith("image/") or row["mime_type"] == "application/pdf"
    disposition = "inline" if inline == 1 and safe_inline else "attachment"
    return FileResponse(
        path,
        media_type=row["mime_type"] or mimetypes.guess_type(row["original_name"])[0],
        filename=row["original_name"],
        content_disposition_type=disposition,
    )


@app.post("/quests/new", name="quest_new")
def quest_new(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    deliverable: str = Form(""),
    difficulty: int = Form(1),
    workspace_id: str = Form(""),
):
    title = title.strip()
    if not title:
        flash(request, "任务标题不能为空。", "error")
        return redirect("cultivation_page", request)
    difficulty_value = max(1, min(int(difficulty or 1), 3))
    workspace_value = int(workspace_id) if workspace_id.isdigit() else None
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO quests(
                title,description,deliverable,difficulty,xp,status,workspace_id,created_at,updated_at
            ) VALUES (?,?,?,?,?,'planned',?,?,?)
            """,
            (
                title,
                description.strip(),
                deliverable.strip(),
                difficulty_value,
                fixed_cultivation_xp(difficulty_value),
                workspace_value,
                ts,
                ts,
            ),
        )
        conn.commit()
    flash(request, "已建立独立修炼任务；它不会自动占用某一天。")
    return RedirectResponse(url=str(request.url_for("cultivation_page")) + "#quests", status_code=303)


@app.post("/quests/{quest_id}/toggle", name="quest_toggle")
def quest_toggle(request: Request, quest_id: int, evidence: str = Form("")):
    with connect() as conn:
        quest = conn.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        if not quest:
            raise HTTPException(status_code=404)
        completing = not bool(quest["completed"])
        if completing and len(evidence.strip()) < 3:
            flash(request, "修炼任务需要先填写验收证据，再标记完成。", "error")
            return RedirectResponse(
                url=request.headers.get("referer") or str(request.url_for("cultivation_page")),
                status_code=303,
            )
        conn.execute(
            """
            UPDATE quests
            SET completed=?,status=?,evidence=?,completed_at=?,updated_at=?
            WHERE id=?
            """,
            (
                1 if completing else 0,
                "done" if completing else "active",
                evidence.strip() if completing else str(quest["evidence"] or ""),
                now_iso() if completing else None,
                now_iso(),
                quest_id,
            ),
        )
        if completing and not int(quest["xp_awarded"] or 0):
            conn.execute(
                "INSERT INTO activities(action, xp, detail, created_at) VALUES (?, ?, ?, ?)",
                ("quest", quest["xp"], f"完成任务：{quest['title']}", now_iso()),
            )
            conn.execute("UPDATE quests SET xp_awarded=1 WHERE id=?", (quest_id,))
        elif not completing and int(quest["xp_awarded"] or 0):
            conn.execute(
                "INSERT INTO activities(action, xp, detail, created_at) VALUES (?, ?, ?, ?)",
                ("quest_reopen", -quest["xp"], f"重新开启任务：{quest['title']}", now_iso()),
            )
            conn.execute("UPDATE quests SET xp_awarded=0 WHERE id=?", (quest_id,))
        conn.commit()
    return RedirectResponse(url=request.headers.get("referer") or str(request.url_for("dashboard")), status_code=303)

@app.post("/quests/{quest_id}/delete", name="quest_delete")
def quest_delete(request: Request, quest_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM quests WHERE id=?", (quest_id,))
        conn.commit()
    return RedirectResponse(url=request.headers.get("referer") or str(request.url_for("dashboard")), status_code=303)
