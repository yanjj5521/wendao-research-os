from __future__ import annotations

from fastapi import APIRouter

from web.runtime import (
    APP_VERSION,
    Any,
    BACKUP_DIR,
    CULTIVATION_DIFFICULTY_LABELS,
    DB_PATH,
    DISTRIBUTION_ROOT,
    File,
    FileResponse,
    Form,
    HTMLResponse,
    HTTPException,
    JSONResponse,
    Path,
    REALM_INDEX,
    REALM_STAGES,
    RedirectResponse,
    Request,
    SIMULATION_DIR,
    STORAGE_ROOT,
    USER_CONFIG_DIR,
    UploadFile,
    _number,
    achievements,
    activity_streak,
    configured_realm_names,
    connect,
    context,
    csv,
    current_realm,
    datetime,
    find_lammps_files,
    flash,
    get_setting,
    io,
    json,
    log_activity,
    now_iso,
    parse_json,
    parse_lammps_log,
    passed_tribulation_keys,
    redirect,
    secure_filename,
    shutil,
    sqlite3,
    tempfile,
    templates,
    total_xp,
    unpack_lammps_bundle,
    uuid,
    zipfile,
)
from services.file_storage import MAX_UPLOAD_BYTES, save_upload_limited

app = APIRouter()

@app.get("/experiments", response_class=HTMLResponse, name="experiments_page")
def experiments_page(request: Request, edit: int = 0, status: str = "", workspace: int = 0):
    with connect() as conn:
        params: list[Any] = []
        sql = "SELECT * FROM experiments WHERE 1=1"
        selected_workspace = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace,)).fetchone() if workspace else None
        if selected_workspace:
            sql += " AND workspace_id=?"
            params.append(workspace)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY experiment_date DESC, updated_at DESC"
        items = [dict(row) for row in conn.execute(sql, params)]
        edit_row = conn.execute("SELECT * FROM experiments WHERE id=?", (edit,)).fetchone() if edit else None
        editing = dict(edit_row) if edit_row else None
        docs = [dict(row) for row in conn.execute("SELECT id,title FROM entries ORDER BY updated_at DESC LIMIT 300")]
    return templates.TemplateResponse(
        request=request,
        name="experiments.html",
        context=context(
            request,
            "experiments",
            items=items,
            editing=editing,
            selected_status=status,
            documents=docs,
            workspace=dict(selected_workspace) if selected_workspace else None,
            active_workspace_id=workspace or None,
        ),
    )


@app.post("/experiments/save", name="experiment_save")
def experiment_save(
    request: Request,
    experiment_id: int = Form(0), sample_id: str = Form(...), experiment_date: str = Form(""), title: str = Form(""), status: str = Form("planned"),
    conditions: str = Form(""), measurements: str = Form(""),
    hypothesis: str = Form(""), observations: str = Form(""), conclusion: str = Form(""),
    next_step: str = Form(""), tags: str = Form(""), attachment_entry_id: str = Form(""), workspace_id: str = Form(""),
):
    sample_id = sample_id.strip()
    if not sample_id:
        flash(request, "样品编号不能为空。", "error")
        return redirect("experiments_page", request)
    workspace_value = int(workspace_id) if workspace_id.isdigit() else None
    values = (
        sample_id, experiment_date or None, title.strip(), status,
        conditions.strip(), measurements.strip(),
        hypothesis.strip(), observations.strip(), conclusion.strip(), next_step.strip(), tags.strip(),
        int(attachment_entry_id) if attachment_entry_id.isdigit() else None,
        workspace_value, now_iso(),
    )
    with connect() as conn:
        try:
            if experiment_id:
                conn.execute("""UPDATE experiments SET sample_id=?,experiment_date=?,title=?,status=?,conditions=?,measurements=?,hypothesis=?,observations=?,conclusion=?,next_step=?,tags=?,attachment_entry_id=?,workspace_id=?,updated_at=? WHERE id=?""", values + (experiment_id,))
                action, xp = "experiment_update", 4
            else:
                conn.execute("""INSERT INTO experiments(sample_id,experiment_date,title,status,conditions,measurements,hypothesis,observations,conclusion,next_step,tags,attachment_entry_id,workspace_id,updated_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values + (now_iso(),))
                action, xp = "experiment_create", 28
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", (action, xp, f"通用DIY实验：{sample_id}", now_iso()))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(request, "样品编号已存在，请换一个编号。", "error")
            return redirect("experiments_page", request)
    flash(request, "实验记录已保存。")
    url = str(request.url_for("experiments_page"))
    return RedirectResponse(f"{url}?workspace={workspace_value}" if workspace_value else url, status_code=303)


@app.post("/experiments/{experiment_id}/delete", name="experiment_delete")
def experiment_delete(request: Request, experiment_id: int):
    with connect() as conn:
        row = conn.execute("SELECT sample_id,workspace_id FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        conn.execute("DELETE FROM experiments WHERE id=?", (experiment_id,))
        conn.commit()
    flash(request, f"已删除实验记录：{row['sample_id'] if row else experiment_id}")
    url = str(request.url_for("experiments_page"))
    return RedirectResponse(f"{url}?workspace={row['workspace_id']}" if row and row["workspace_id"] else url, status_code=303)


EXPERIMENT_EXPORT_FIELDS = (
    "id", "sample_id", "experiment_date", "title", "status",
    "conditions", "measurements", "hypothesis", "observations",
    "conclusion", "next_step", "tags", "workspace_id", "created_at", "updated_at",
)


@app.get("/experiments/export.csv", name="experiments_export")
def experiments_export(workspace: int = 0):
    columns = ", ".join(EXPERIMENT_EXPORT_FIELDS)
    with connect() as conn:
        if workspace:
            query = conn.execute(
                f"SELECT {columns} FROM experiments WHERE workspace_id=? ORDER BY experiment_date,id",
                (workspace,),
            )
        else:
            query = conn.execute(
                f"SELECT {columns} FROM experiments ORDER BY experiment_date,id"
            )
        rows = [dict(row) for row in query]
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(EXPERIMENT_EXPORT_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    path = BACKUP_DIR / f"experiments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path.write_text("\ufeff" + output.getvalue(), encoding="utf-8")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/simulations", response_class=HTMLResponse, name="simulations_page")
def simulations_page(request: Request, workspace: int = 0):
    with connect() as conn:
        items = []
        selected_workspace = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace,)).fetchone() if workspace else None
        if selected_workspace:
            rows = conn.execute(
                "SELECT * FROM simulations WHERE workspace_id=? ORDER BY updated_at DESC", (workspace,)
            )
        else:
            rows = conn.execute("SELECT * FROM simulations ORDER BY updated_at DESC")
        for row in rows:
            item = dict(row)
            item["summary"] = parse_json(item.get("summary_json", "{}"), {})
            item["files"] = [dict(x) for x in conn.execute("SELECT * FROM simulation_files WHERE simulation_id=? ORDER BY role,original_name", (row["id"],))]
            items.append(item)
    return templates.TemplateResponse(
        request=request,
        name="simulations.html",
        context=context(
            request,
            "simulations",
            items=items,
            workspace=dict(selected_workspace) if selected_workspace else None,
            active_workspace_id=workspace or None,
        ),
    )


@app.post("/simulations/new", name="simulation_new")
def simulation_new(
    request: Request, case_name: str = Form(...), project_name: str = Form(""), ensemble: str = Form(""), forcefield: str = Form(""),
    temperature: str = Form(""), timestep: str = Form(""), run_command: str = Form(""), notes: str = Form(""), tags: str = Form(""),
    workspace_id: str = Form(""), files: list[UploadFile] = File(default=[]),
):
    case_name = case_name.strip()
    if not case_name:
        flash(request, "案例名称不能为空。", "error")
        return redirect("simulations_page", request)
    folder_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{secure_filename(case_name)}"
    folder = SIMULATION_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    all_paths: list[Path] = []
    try:
        used_bytes = 0
        for upload in [f for f in files if f.filename]:
            target = folder / secure_filename(upload.filename or "file")
            used_bytes += save_upload_limited(
                upload,
                target,
                remaining_bytes=MAX_UPLOAD_BYTES - used_bytes,
            )
            if target.suffix.lower() == ".zip":
                unpacked_dir = folder / target.stem
                all_paths.extend(unpack_lammps_bundle(target, unpacked_dir))
            else:
                all_paths.append(target)
        categorized = find_lammps_files(all_paths)
        parsed: dict[str, Any] = {}
        if categorized["logs"]:
            parsed = parse_lammps_log(categorized["logs"][0])
        ts = now_iso()
        workspace_value = int(workspace_id) if workspace_id.isdigit() else None
        with connect() as conn:
            cur = conn.execute("""INSERT INTO simulations(case_name,project_name,status,engine_version,ensemble,forcefield,atoms,steps,temperature,timestep,last_step,last_temp,last_etotal,warnings,errors,run_command,notes,tags,folder_path,summary_json,workspace_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                case_name, project_name.strip(), parsed.get("status", "NEW"), parsed.get("lammps_version", ""), ensemble.strip(), forcefield.strip(), parsed.get("atoms"), parsed.get("steps"), _number(temperature), _number(timestep), parsed.get("last_step"), parsed.get("last_temp"), parsed.get("last_etotal"), parsed.get("warnings", 0), parsed.get("errors", 0), run_command.strip(), notes.strip(), tags.strip(), folder_name, json.dumps(parsed, ensure_ascii=False), workspace_value, ts, ts,
            ))
            sim_id = int(cur.lastrowid)
            role_map = {id(path): role for role, paths in categorized.items() for path in paths}
            for path in all_paths:
                conn.execute("INSERT INTO simulation_files(simulation_id,role,file_path,original_name,file_size,created_at) VALUES (?,?,?,?,?,?)", (sim_id, role_map.get(id(path), "other"), str(path.relative_to(folder)), path.name, path.stat().st_size, ts))
            conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("simulation_create", 34, f"归档LAMMPS案例：{case_name}", ts))
            conn.commit()
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        flash(request, f"模拟案例归档失败：{exc}", "error")
        return redirect("simulations_page", request)
    flash(request, "LAMMPS案例已归档并完成日志解析。")
    url = str(request.url_for("simulations_page"))
    return RedirectResponse(f"{url}?workspace={workspace_value}" if workspace_value else url, status_code=303)


@app.post("/simulations/{simulation_id}/update", name="simulation_update")
def simulation_update(
    request: Request,
    simulation_id: int,
    status: str = Form("NEW"),
    notes: str = Form(""),
    tags: str = Form(""),
    workspace_id: str = Form(""),
):
    workspace_value = int(workspace_id) if workspace_id.isdigit() else None
    with connect() as conn:
        conn.execute(
            "UPDATE simulations SET status=?,notes=?,tags=?,workspace_id=?,updated_at=? WHERE id=?",
            (status, notes.strip(), tags.strip(), workspace_value, now_iso(), simulation_id),
        )
        conn.commit()
    flash(request, "模拟案例已更新。")
    url = str(request.url_for("simulations_page"))
    return RedirectResponse(f"{url}?workspace={workspace_value}" if workspace_value else url, status_code=303)


@app.post("/simulations/{simulation_id}/delete", name="simulation_delete")
def simulation_delete(request: Request, simulation_id: int):
    with connect() as conn:
        row = conn.execute("SELECT folder_path,case_name,workspace_id FROM simulations WHERE id=?", (simulation_id,)).fetchone()
        conn.execute("DELETE FROM simulations WHERE id=?", (simulation_id,))
        conn.commit()
    if row:
        shutil.rmtree(SIMULATION_DIR / row["folder_path"], ignore_errors=True)
    flash(request, "模拟案例已删除。")
    url = str(request.url_for("simulations_page"))
    return RedirectResponse(f"{url}?workspace={row['workspace_id']}" if row and row["workspace_id"] else url, status_code=303)


@app.get("/simulations/{simulation_id}/files/{file_id}", name="simulation_file")
def simulation_file(simulation_id: int, file_id: int):
    with connect() as conn:
        row = conn.execute("SELECT s.folder_path,f.file_path,f.original_name FROM simulation_files f JOIN simulations s ON s.id=f.simulation_id WHERE f.id=? AND f.simulation_id=?", (file_id, simulation_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    base = (SIMULATION_DIR / row["folder_path"]).resolve()
    path = (base / row["file_path"]).resolve()
    if base not in path.parents or not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, filename=row["original_name"])


@app.get("/cultivation", response_class=HTMLResponse, name="cultivation_page")
def cultivation_page(request: Request, workspace: int = 0):
    with connect() as conn:
        selected_workspace = conn.execute(
            "SELECT id,name,icon FROM workspaces WHERE id=? AND active=1",
            (workspace,),
        ).fetchone() if workspace else None
        xp = total_xp(conn)
        activity_rows = [dict(row) for row in conn.execute("SELECT action,SUM(xp) xp,COUNT(*) n FROM activities GROUP BY action ORDER BY xp DESC")]
        totals = dict(conn.execute("""SELECT COUNT(*) total, COALESCE(SUM(CASE WHEN trim(tags)!='' THEN 1 ELSE 0 END),0) tagged, COALESCE(SUM(CASE WHEN trim(summary)!='' THEN 1 ELSE 0 END),0) summarized, COALESCE(SUM(CASE WHEN analysis_json!='{}' THEN 1 ELSE 0 END),0) analyzed FROM entries""").fetchone())
        exp_total = conn.execute("SELECT COUNT(*) n FROM experiments").fetchone()["n"]
        sim_total = conn.execute("SELECT COUNT(*) n FROM simulations").fetchone()["n"]
        total_assets = totals["total"] + exp_total + sim_total
        quality_points = totals["tagged"] * 2 + totals["summarized"] * 2 + totals["analyzed"] * 4 + exp_total * 5 + sim_total * 5
        quality = min(100, round(quality_points / max(total_assets * 5, 1) * 100))
        quest_sql = """
            SELECT q.*,w.name workspace_name,
                (SELECT COUNT(*) FROM daily_missions m WHERE m.quest_id=q.id) linked_total,
                (SELECT COALESCE(SUM(m.completed),0) FROM daily_missions m WHERE m.quest_id=q.id) linked_done
            FROM quests q
            LEFT JOIN workspaces w ON w.id=q.workspace_id
        """
        quest_params: list[Any] = []
        if selected_workspace:
            quest_sql += " WHERE q.workspace_id=?"
            quest_params.append(workspace)
        quest_sql += " ORDER BY q.completed,q.difficulty DESC,q.updated_at DESC,q.id DESC"
        quests = [
            dict(row)
            for row in conn.execute(quest_sql, quest_params)
        ]
        workspaces = [
            dict(row)
            for row in conn.execute("SELECT id,name FROM workspaces WHERE active=1 ORDER BY sort_order,id")
        ]
        ach = achievements(conn)
        streak = activity_streak(conn)
        passed_gates = passed_tribulation_keys(conn)
    realm = current_realm(xp, passed_gates)
    labels = configured_realm_names()
    current_index = REALM_INDEX[realm["key"]]
    realm_path = [
        {
            "key": stage.key,
            "name": labels[stage.key],
            "threshold": stage.threshold,
            "required_xp": stage.required_xp,
            "description": stage.description,
            "state": (
                "current"
                if stage.key == realm["key"]
                else (
                    "passed"
                    if REALM_INDEX[stage.key] < current_index
                    else (
                        "tribulation"
                        if realm["tribulation_required"]
                        and stage.key == realm["next_key"]
                        else "locked"
                    )
                )
            ),
        }
        for stage in REALM_STAGES
    ]
    return templates.TemplateResponse(
        request=request,
        name="cultivation.html",
        context=context(
            request,
            "cultivation",
            xp=xp,
            realm=realm,
            realm_path=realm_path,
            streak=streak,
            activity_rows=activity_rows,
            totals=totals,
            exp_total=exp_total,
            sim_total=sim_total,
            quality=quality,
            quests=quests,
            workspaces=workspaces,
            difficulty_labels=CULTIVATION_DIFFICULTY_LABELS,
            achievements=ach,
            selected_workspace=(
                dict(selected_workspace) if selected_workspace else None
            ),
            active_workspace_id=(
                int(selected_workspace["id"]) if selected_workspace else None
            ),
        ),
    )



@app.get("/portable", name="portable_export")
def portable_export():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = BACKUP_DIR / f"ResearchCultivationOS_portable_{timestamp}.zip"
    excluded_dirs = {
        ".venv", "__pycache__", ".git", "backups", "autobackups", "build", "dist",
        "instance", "storage", "user_data",
    }
    excluded_suffixes = {".pyc", ".pyo"}
    program_root = DISTRIBUTION_ROOT
    portable_storage_names = (
        "uploads", "simulations", "research_foundation", "deliveries",
        "note_images", "profile", "sync_exports",
    )
    with tempfile.TemporaryDirectory() as tmp:
        consistent_db = Path(tmp) / DB_PATH.name
        if DB_PATH.exists():
            source_conn = connect()
            destination_conn = sqlite3.connect(consistent_db)
            try:
                source_conn.backup(destination_conn)
            finally:
                destination_conn.close()
                source_conn.close()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in program_root.rglob("*"):
                relative = path.relative_to(program_root)
                if any(part in excluded_dirs for part in relative.parts):
                    continue
                if path == archive or path.suffix.lower() in excluded_suffixes:
                    continue
                if path.is_file():
                    zf.write(path, Path("ResearchCultivationOS") / relative)
            if consistent_db.exists():
                zf.write(
                    consistent_db,
                    "ResearchCultivationOS/user_data/instance/research_os.db",
                )
            for name in portable_storage_names:
                source_dir = STORAGE_ROOT / name
                if not source_dir.exists():
                    continue
                for path in source_dir.rglob("*"):
                    if path.is_file() and path != archive:
                        relative = path.relative_to(source_dir)
                        zf.write(
                            path,
                            Path("ResearchCultivationOS/user_data/storage") / name / relative,
                        )
            if USER_CONFIG_DIR.exists():
                for path in USER_CONFIG_DIR.rglob("*"):
                    if path.is_file():
                        relative = path.relative_to(USER_CONFIG_DIR)
                        zf.write(
                            path,
                            Path("ResearchCultivationOS/user_data/user_config") / relative,
                        )
            zf.writestr("ResearchCultivationOS/portable.flag", "portable-data-v1\n")
            manifest = {
                "name": "科研系统",
                "version": get_setting("portable_version", APP_VERSION),
                "created_at": now_iso(),
                "instructions": "完整解压后启动。portable.flag 会让数据继续保存在 user_data 中，不写入系统目录。",
            }
            zf.writestr("ResearchCultivationOS/PORTABLE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    log_activity("portable_export", 5, "生成整套便携迁移包")
    return FileResponse(archive, media_type="application/zip", filename=archive.name)


@app.get("/api/stats", name="api_stats")
def api_stats():
    with connect() as conn:
        xp = total_xp(conn)
        return JSONResponse(
            {
                "entries": conn.execute("SELECT COUNT(*) n FROM entries").fetchone()["n"],
                "xp": xp,
                "realm": current_realm(xp),
                "streak": activity_streak(conn),
            }
        )
