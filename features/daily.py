from __future__ import annotations

import json
import mimetypes
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from core.runtime_paths import STORAGE_ROOT
from services.economy import award_mission, balance, balances, transact
from services.file_storage import MAX_UPLOAD_BYTES, save_upload_limited
from services.plan_import import PlanSpec, build_default_plan, parse_plan_text, render_plan_text
from services.prompt_builder import build_plan_prompt, current_state
from services.online_sync import best_effort_sync, queue_event
from services.progression import fixed_cultivation_xp, fixed_daily_xp

DELIVERY_DIR = STORAGE_ROOT / "deliveries"
DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
POSTPONE_COST = 2

THOUGHT_EGGS = [
    "如果把你今天的研究对象缩小到一个界面、一个孔或一条路径，最关键的问题会变成什么？",
    "你现在相信的结论，最可能被哪一种反例推翻？",
    "如果性能没有提高，这次实验仍然能回答哪个有价值的问题？",
    "把‘材料更好’改写成三个可测量、可证伪的判断。",
    "电子走得更快时，离子一定更容易到达吗？请构造一个相反情形。",
    "如果只能保留一张图来讲清你的研究，你会保留哪张？它必须证明什么？",
    "今天学到的概念能否迁移到另一个尺度：分子、孔隙、试件或器件？",
    "假设导师不同意你的解释，你最先补哪一条证据，而不是补更多文字？",
    "你今天的交付中，哪一部分未来可以直接成为论文方法、图或补充材料？",
    "把一个模糊问题改写成：变量A如何通过机制M影响结果B？",
    "如果把当前路线交给师弟师妹，哪一步最容易因隐性经验而失败？",
    "你是在优化一个数值，还是在识别一个限制步骤？二者的实验设计有何不同？",
]

TRACK_KEYWORDS = {
    "电化学": ["电化学", "电势", "cv", "gcd", "eis", "电极", "nernst"],
    "超级电容器": ["超级电容", "电容", "edlc", "赝电容", "储能"],
    "水泥基能源材料": ["水泥", "c-s-h", "孔结构", "器件"],
    "膨胀石墨": ["膨胀石墨", "eg", "石墨", "导电网络"],
    "分子动力学": ["分子动力学", "md", "lammps", "轨迹", "力场"],
    "机器学习": ["机器学习", "ml", "pandas", "回归", "数据集"],
    "科研写作": ["论文", "figure", "写作", "汇报", "审稿"],
    "英语与雅思": ["英语", "雅思", "ielts", "词汇", "英文"],
}


def _infer_track_id(conn, category: str, title: str, description: str = "") -> int | None:
    text = f"{category} {title} {description}".lower()
    tracks = {row["name"]: int(row["id"]) for row in conn.execute("SELECT id,name FROM research_tracks WHERE active=1")}
    for track_name, keywords in TRACK_KEYWORDS.items():
        if track_name in tracks and any(keyword.lower() in text for keyword in keywords):
            return tracks[track_name]
    return None


def _insert_plan(conn, spec: PlanSpec, make_active: bool = True) -> int:
    ts = now_iso()
    quest_ids = {
        str(row["title"]).strip(): int(row["id"])
        for row in conn.execute("SELECT id,title FROM quests WHERE completed=0")
    }
    workspaces = {
        str(row["name"]).strip().lower(): int(row["id"])
        for row in conn.execute("SELECT id,name FROM workspaces WHERE active=1")
    }
    projects = {
        str(row["title"]).strip().lower(): int(row["id"])
        for row in conn.execute("SELECT id,title FROM research_projects WHERE status='active'")
    }
    for task in spec.cultivation_tasks:
        title = task.title.strip()
        if not title or title in quest_ids:
            continue
        difficulty = max(1, min(int(task.difficulty or 1), 3))
        workspace_id = workspaces.get(task.workspace_name.strip().lower()) if task.workspace_name else None
        cur = conn.execute(
            """
            INSERT INTO quests(
                title,description,deliverable,difficulty,xp,status,workspace_id,created_at,updated_at
            ) VALUES (?,?,?,?,?,'planned',?,?,?)
            """,
            (
                title,
                task.description.strip(),
                task.deliverable.strip(),
                difficulty,
                fixed_cultivation_xp(difficulty),
                workspace_id,
                ts,
                ts,
            ),
        )
        quest_ids[title] = int(cur.lastrowid)
    if make_active:
        conn.execute("UPDATE study_plans SET status='archived' WHERE status='active'")
    cur = conn.execute(
        "INSERT INTO study_plans(name,description,current_day,total_days,status,source_text,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            spec.name,
            spec.description,
            1,
            max(day.index for day in spec.days),
            "active" if make_active else "archived",
            render_plan_text(spec),
            ts,
            ts,
        ),
    )
    plan_id = int(cur.lastrowid)
    for day in spec.days:
        for order, mission in enumerate(day.missions):
            track_id = _infer_track_id(conn, mission.category, mission.title, mission.description)
            quest_id = quest_ids.get(mission.cultivation_title.strip()) if mission.cultivation_title else None
            workspace_id = (
                workspaces.get(mission.workspace_name.strip().lower())
                if mission.workspace_name
                else None
            )
            project_id = (
                projects.get(mission.project_name.strip().lower())
                if mission.project_name
                else None
            )
            conn.execute(
                """INSERT INTO daily_missions(
                    plan_id,day_index,category,title,description,deliverable,duration_minutes,xp,
                    optional,track_id,quest_id,workspace_id,project_id,sort_order,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    plan_id, day.index, mission.category, mission.title, mission.description,
                    mission.deliverable, mission.duration_minutes, fixed_daily_xp(mission.duration_minutes),
                    int(mission.optional), track_id, quest_id, workspace_id, project_id,
                    order, ts, ts,
                ),
            )
    return plan_id


def _activate_plan(conn, plan_id: int) -> bool:
    row = conn.execute("SELECT id FROM study_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        return False
    ts = now_iso()
    conn.execute(
        "UPDATE study_plans SET status='archived' WHERE status='active' AND id<>?",
        (plan_id,),
    )
    conn.execute(
        "UPDATE study_plans SET status='active',updated_at=? WHERE id=?",
        (ts, plan_id),
    )
    return True


def _plan_health(conn, plan_id: int) -> dict[str, Any]:
    days = [
        dict(row)
        for row in conn.execute(
            """
            SELECT day_index,
                   SUM(CASE WHEN optional=0 THEN 1 ELSE 0 END) required_count,
                   SUM(CASE WHEN optional=1 THEN 1 ELSE 0 END) optional_count,
                   SUM(CASE WHEN optional=0 THEN duration_minutes ELSE 0 END) required_minutes,
                   SUM(CASE WHEN optional=0 AND completed=1 THEN 1 ELSE 0 END) required_done,
                   SUM(CASE WHEN trim(deliverable)='' THEN 1 ELSE 0 END) missing_deliverables
            FROM daily_missions
            WHERE plan_id=?
            GROUP BY day_index
            ORDER BY day_index
            """,
            (plan_id,),
        )
    ]
    warnings: list[str] = []
    for day in days:
        index = int(day["day_index"])
        required = int(day["required_count"] or 0)
        optional = int(day["optional_count"] or 0)
        minutes = int(day["required_minutes"] or 0)
        missing = int(day["missing_deliverables"] or 0)
        if required < 1 or required > 3:
            warnings.append(f"Day {index} 有 {required} 项必做，建议保持 1–3 项。")
        if optional > 1:
            warnings.append(f"Day {index} 有 {optional} 项可选，建议最多 1 项。")
        if minutes < 60 or minutes > 150:
            warnings.append(f"Day {index} 必做共 {minutes} 分钟，建议控制在 60–150 分钟。")
        if missing:
            warnings.append(f"Day {index} 有 {missing} 项尚未填写最小交付。")
    total = sum(int(day["required_count"] or 0) for day in days)
    done = sum(int(day["required_done"] or 0) for day in days)
    return {
        "days": days,
        "warnings": warnings[:12],
        "score": max(0, 100 - min(100, len(warnings) * 12)),
        "required_total": total,
        "required_done": done,
        "completion": round(done / total * 100) if total else 0,
        "balanced_days": sum(
            1
            for day in days
            if 1 <= int(day["required_count"] or 0) <= 3
            and int(day["optional_count"] or 0) <= 1
            and 60 <= int(day["required_minutes"] or 0) <= 150
            and int(day["missing_deliverables"] or 0) == 0
        ),
    }


def ensure_default_plan() -> None:
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) n FROM study_plans").fetchone()["n"]
        if count == 0:
            _insert_plan(conn, build_default_plan(), make_active=True)
            conn.commit()


def _safe_relative(value: str, fallback: str) -> str:
    value = (value or fallback).replace("\\", "/")
    parts = [p for p in PurePosixPath(value).parts if p not in {"", ".", "..", "/"}]
    clean = "/".join(re.sub(r"[^\w.\-（）()\u4e00-\u9fff ]+", "_", p).strip() or "file" for p in parts)
    return clean[:700] or fallback


def _save_upload(
    upload: UploadFile,
    root: Path,
    relative: str,
    remaining_bytes: int,
) -> tuple[str, str, str, int]:
    relative = _safe_relative(relative, upload.filename or "file")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}_{uuid.uuid4().hex[:6]}{target.suffix}")
        relative = target.relative_to(root).as_posix()
    size = save_upload_limited(upload, target, remaining_bytes=remaining_bytes)
    mime = upload.content_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return relative, relative, upload.filename or target.name, size, mime


def _unlock_delivery_eggs(conn) -> None:
    count = conn.execute("SELECT COUNT(*) n FROM mission_deliveries").fetchone()["n"]
    if count >= 1:
        conn.execute("UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='first_delivery'", (now_iso(),))
    if count >= 7:
        conn.execute("UPDATE easter_eggs SET unlocked=1,discovered_at=COALESCE(discovered_at,?) WHERE egg_key='seven_deliveries'", (now_iso(),))


def register_daily_routes(app, templates, context: Callable[..., dict[str, Any]], flash: Callable[[Request, str, str], None]):
    ensure_default_plan()
    router = APIRouter()

    def go(request: Request, route_name: str, **params: Any) -> RedirectResponse:
        return RedirectResponse(request.url_for(route_name, **params), status_code=303)

    @router.get("/daily", response_class=HTMLResponse, name="daily_page")
    def daily_page(request: Request, day: int | None = None):
        with connect() as conn:
            plan = conn.execute("SELECT * FROM study_plans WHERE status='active' ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not plan:
                ensure_default_plan()
                plan = conn.execute("SELECT * FROM study_plans WHERE status='active' ORDER BY updated_at DESC LIMIT 1").fetchone()
            current_day = max(1, min(int(day or plan["current_day"]), int(plan["total_days"])))
            missions = []
            for row in conn.execute(
                """SELECT m.*,t.name track_name,t.icon track_icon,q.title cultivation_title,
                    w.name workspace_name,w.icon workspace_icon,p.title project_title,
                    (SELECT COUNT(*) FROM mission_deliveries d WHERE d.mission_id=m.id) delivery_count
                   FROM daily_missions m
                   LEFT JOIN research_tracks t ON t.id=m.track_id
                   LEFT JOIN quests q ON q.id=m.quest_id
                   LEFT JOIN workspaces w ON w.id=m.workspace_id
                   LEFT JOIN research_projects p ON p.id=m.project_id
                   WHERE m.plan_id=? AND m.day_index=? ORDER BY m.optional,m.sort_order,m.id""",
                (plan["id"], current_day),
            ):
                item = dict(row)
                item["deliveries"] = [dict(d) for d in conn.execute(
                    "SELECT * FROM mission_deliveries WHERE mission_id=? ORDER BY created_at DESC", (row["id"],)
                )]
                missions.append(item)
            log = conn.execute("SELECT * FROM daily_logs WHERE plan_id=? AND day_index=?", (plan["id"], current_day)).fetchone()
            required = [m for m in missions if not m["optional"]]
            done = sum(int(m["completed"]) for m in required)
            progress = round(done / len(required) * 100) if required else 0
            total_minutes = sum(int(m["duration_minutes"]) for m in required if not m["completed"])
            day_title = required[0]["title"] if required else f"第 {current_day} 日"
            recent_days = [dict(row) for row in conn.execute(
                """SELECT day_index,
                    SUM(CASE WHEN optional=0 THEN 1 ELSE 0 END) required_count,
                    SUM(CASE WHEN optional=0 AND completed=1 THEN 1 ELSE 0 END) done_count
                   FROM daily_missions WHERE plan_id=? GROUP BY day_index ORDER BY day_index""",
                (plan["id"],),
            )]
            wallet = balances(conn)
            egg = THOUGHT_EGGS[(current_day - 1) % len(THOUGHT_EGGS)]
        return templates.TemplateResponse(
            request=request,
            name="daily.html",
            context=context(
                request, "daily", plan=dict(plan), missions=missions, current_day=current_day,
                progress=progress, total_minutes=total_minutes, day_title=day_title,
                day_log=dict(log) if log else None, day_map=recent_days, wallet=wallet,
                postpone_cost=POSTPONE_COST, thought_egg=egg,
            ),
        )

    @router.post("/daily/quick-note", name="daily_quick_note")
    def daily_quick_note(
        request: Request,
        title: str = Form(""),
        note: str = Form(...),
        source: str = Form("随手推进"),
    ):
        """A plan-free path: leave one useful trace without earning a checklist debt."""
        note = note.strip()
        if len(note) < 3:
            flash(request, "至少写下一个事实、判断或下一步，再归档。", "error")
            return RedirectResponse(request.url_for("daily_page"), status_code=303)
        title = title.strip()[:160] or (note.replace("\n", " ")[:42].rstrip("。；，, ") or "今日推进")
        ts = now_iso()
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO entries(title,kind,domain,tags,summary,content,source,content_format,created_at,updated_at)
                   VALUES (?,'note','随手推进','轻量记录,待更新',?,?,?,?,?,?)""",
                (title, note[:280], note[:6000], source.strip()[:400], "plain", ts, ts),
            )
            entry_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO activities(action,entry_id,xp,detail,created_at) VALUES ('quick_progress',?,3,?,?)",
                (entry_id, f"随手归档：{title}", ts),
            )
            conn.commit()
        flash(request, "已归档到知识库；它不是待办债务，以后随时可以编辑、补证据或推翻。")
        return RedirectResponse(request.url_for("entry_view", entry_id=entry_id), status_code=303)

    @router.post("/daily/missions/{mission_id}/deliver", name="daily_delivery_submit")
    def daily_delivery_submit(
        request: Request,
        mission_id: int,
        view_day: int = Form(...),
        note: str = Form(""),
        review_text: str = Form(""),
        folder_paths: str = Form("[]"),
        files: list[UploadFile] = File(default=[]),
        folder_files: list[UploadFile] = File(default=[]),
    ):
        valid_files = [f for f in files if f.filename]
        valid_folder_files = [f for f in folder_files if f.filename]
        note = note.strip()
        review_text = review_text.strip()
        if not note and not review_text and not valid_files and not valid_folder_files:
            flash(request, "请至少提交一段文字、一个文件或一个文件夹。", "error")
            return RedirectResponse(f"{request.url_for('daily_page')}?day={view_day}", status_code=303)
        try:
            paths = json.loads(folder_paths or "[]")
            if not isinstance(paths, list):
                paths = []
        except json.JSONDecodeError:
            paths = []
        storage_key = f"mission_{mission_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        root = DELIVERY_DIR / storage_key
        root.mkdir(parents=True, exist_ok=True)
        saved: list[tuple[str, str, str, int, str]] = []
        try:
            used_bytes = 0
            for upload in valid_files:
                item = _save_upload(
                    upload,
                    root,
                    upload.filename or "file",
                    MAX_UPLOAD_BYTES - used_bytes,
                )
                saved.append(item)
                used_bytes += item[3]
            for index, upload in enumerate(valid_folder_files):
                rel = paths[index] if index < len(paths) else upload.filename or f"folder_file_{index+1}"
                item = _save_upload(upload, root, rel, MAX_UPLOAD_BYTES - used_bytes)
                saved.append(item)
                used_bytes += item[3]
            if note:
                text_target = root / "文字交付.txt"
                text_target.write_text(note, encoding="utf-8")
                saved.append(("文字交付.txt", "文字交付.txt", "文字交付.txt", text_target.stat().st_size, "text/plain"))
            if review_text:
                review_target = root / "复盘关键文本.txt"
                review_target.write_text(review_text, encoding="utf-8")
                saved.append(("复盘关键文本.txt", "复盘关键文本.txt", "复盘关键文本.txt", review_target.stat().st_size, "text/plain"))
            ts = now_iso()
            with connect() as conn:
                mission = conn.execute("SELECT * FROM daily_missions WHERE id=?", (mission_id,)).fetchone()
                if not mission:
                    raise ValueError("任务不存在")
                cur = conn.execute(
                    """
                    INSERT INTO mission_deliveries(
                        mission_id,note,review_text,review_source,storage_key,file_count,total_size,created_at
                    ) VALUES (?,?,?,'manual',?,?,?,?)
                    """,
                    (mission_id, note, review_text, storage_key, len(saved), sum(item[3] for item in saved), ts),
                )
                delivery_id = int(cur.lastrowid)
                for rel, stored, original, size, mime in saved:
                    conn.execute(
                        "INSERT INTO mission_delivery_files(delivery_id,relative_path,stored_path,original_name,mime_type,file_size,created_at) VALUES (?,?,?,?,?,?,?)",
                        (delivery_id, rel, stored, original, mime, size, ts),
                    )
                if review_text:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO review_sources(
                            source_type,source_id,title,source_text,storage_key,source_date,created_at
                        ) VALUES ('mission_delivery',?,?,?,?,?,?)
                        """,
                        (delivery_id, mission["title"], review_text, storage_key, ts[:10], ts),
                    )
                # A delivery folder is immutable evidence.  Its accompanying text is a living
                # knowledge card, so the user can revise the interpretation later.
                archive_text = "\n\n".join(
                    part for part in (
                        note,
                        f"复盘关键文本\n{review_text}" if review_text else "",
                    ) if part
                ) or "已提交文件交付；可在原交付包中查看附件。"
                archive_title = f"交付归档 · {mission['title']}"
                archive = conn.execute(
                    """INSERT INTO entries(title,kind,domain,tags,summary,content,source,content_format,created_at,updated_at)
                       VALUES (?,'note','任务交付','交付归档,待更新',?,?,?,?,?,?)""",
                    (
                        archive_title,
                        archive_text[:280],
                        archive_text[:6000],
                        f"每日任务交付 #{delivery_id}；附件保存在交付文件夹 {storage_key}",
                        "plain", ts, ts,
                    ),
                )
                archive_entry_id = int(archive.lastrowid)
                conn.execute(
                    "INSERT INTO activities(action,entry_id,xp,detail,created_at) VALUES ('delivery_archive',?,0,?,?)",
                    (archive_entry_id, f"交付归档：{mission['title']}", ts),
                )
                first_completion = not int(mission["completed"])
                conn.execute("UPDATE daily_missions SET completed=1,completed_at=COALESCE(completed_at,?),updated_at=? WHERE id=?", (ts, ts, mission_id))
                if not int(mission["xp_awarded"]):
                    conn.execute("UPDATE daily_missions SET xp_awarded=1 WHERE id=?", (mission_id,))
                    conn.execute(
                        "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                        ("daily_delivery", int(mission["xp"]), f"交付并完成：{mission['title']}", ts),
                    )
                rewards = {}
                if not int(mission["stones_awarded"]):
                    rewards = award_mission(conn, mission)
                    conn.execute("UPDATE daily_missions SET stones_awarded=1,materials_awarded=1 WHERE id=?", (mission_id,))
                if first_completion:
                    queue_event(
                        conn,
                        "mission_completed",
                        {
                            "mission_id": mission_id,
                            "title": mission["title"],
                            "category": mission["category"],
                            "rewards": rewards,
                            "completed_at": ts,
                        },
                        event_uuid=f"delivery-{storage_key}",
                    )
                _unlock_delivery_eggs(conn)
                conn.commit()
            best_effort_sync()
            reward_text = "、".join(f"{amount}{'灵石' if key=='spirit_stone' else {'spirit_wood':'灵木','mystic_iron':'玄铁','star_sand':'星砂'}[key]}" for key, amount in rewards.items())
            flash(request, f"交付成功，任务已打卡。{'获得 '+reward_text+'。' if reward_text else '成果已追加归档。'}", "success")
        except Exception as exc:
            shutil.rmtree(root, ignore_errors=True)
            flash(request, f"交付失败：{exc}", "error")
        return RedirectResponse(f"{request.url_for('daily_page')}?day={view_day}", status_code=303)

    @router.post("/daily/missions/{mission_id}/postpone", name="daily_mission_postpone")
    def daily_mission_postpone(request: Request, mission_id: int, view_day: int = Form(...)):
        with connect() as conn:
            mission = conn.execute("SELECT * FROM daily_missions WHERE id=?", (mission_id,)).fetchone()
            if not mission:
                raise HTTPException(status_code=404)
            if mission["completed"]:
                flash(request, "已完成的任务无需推迟。", "error")
                return RedirectResponse(f"{request.url_for('daily_page')}?day={view_day}", status_code=303)
            if balance(conn, "spirit_stone") < POSTPONE_COST:
                flash(request, f"灵石不足。推迟一次需要 {POSTPONE_COST} 灵石。", "error")
                return RedirectResponse(f"{request.url_for('daily_page')}?day={view_day}", status_code=303)
            next_day = int(mission["day_index"]) + 1
            plan = conn.execute("SELECT total_days FROM study_plans WHERE id=?", (mission["plan_id"],)).fetchone()
            if next_day > int(plan["total_days"]):
                conn.execute("UPDATE study_plans SET total_days=?,updated_at=? WHERE id=?", (next_day, now_iso(), mission["plan_id"]))
            order = conn.execute("SELECT COALESCE(MAX(sort_order),-1)+1 n FROM daily_missions WHERE plan_id=? AND day_index=?", (mission["plan_id"], next_day)).fetchone()["n"]
            transact(conn, "spirit_stone", -POSTPONE_COST, f"推迟任务：{mission['title']}", mission_id)
            queue_event(
                conn,
                "mission_postponed",
                {"mission_id": mission_id, "title": mission["title"], "cost": POSTPONE_COST, "to_day": next_day},
                event_uuid=f"postpone-{mission_id}-{int(mission['postponed_count'])+1}",
            )
            conn.execute(
                "UPDATE daily_missions SET day_index=?,sort_order=?,postponed_count=postponed_count+1,updated_at=? WHERE id=?",
                (next_day, int(order), now_iso(), mission_id),
            )
            conn.commit()
        best_effort_sync()
        flash(request, f"任务已推迟到 Day {next_day}，消耗 {POSTPONE_COST} 灵石。", "success")
        return RedirectResponse(f"{request.url_for('daily_page')}?day={view_day}", status_code=303)

    @router.get("/daily/deliveries/{delivery_id}/file/{file_id}", name="daily_delivery_file")
    def daily_delivery_file(delivery_id: int, file_id: int):
        with connect() as conn:
            row = conn.execute(
                """SELECT d.storage_key,f.stored_path,f.original_name,f.mime_type
                   FROM mission_delivery_files f JOIN mission_deliveries d ON d.id=f.delivery_id
                   WHERE f.id=? AND d.id=?""", (file_id, delivery_id)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        root = (DELIVERY_DIR / row["storage_key"]).resolve()
        target = (root / row["stored_path"]).resolve()
        if root not in target.parents or not target.exists():
            raise HTTPException(status_code=404)
        return FileResponse(target, filename=row["original_name"], media_type=row["mime_type"])

    @router.get("/daily/deliveries/{delivery_id}/download", name="daily_delivery_download")
    def daily_delivery_download(delivery_id: int):
        with connect() as conn:
            delivery = conn.execute("SELECT * FROM mission_deliveries WHERE id=?", (delivery_id,)).fetchone()
            mission = conn.execute("SELECT m.title FROM daily_missions m JOIN mission_deliveries d ON d.mission_id=m.id WHERE d.id=?", (delivery_id,)).fetchone()
        if not delivery:
            raise HTTPException(status_code=404)
        root = DELIVERY_DIR / delivery["storage_key"]
        archive = DELIVERY_DIR / f"{delivery['storage_key']}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(root))
        filename = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", mission["title"] if mission else "科研交付")[:80] + ".zip"
        return FileResponse(archive, filename=filename, media_type="application/zip")

    @router.post("/daily/log", name="daily_log_save")
    def daily_log_save(request: Request, plan_id: int = Form(...), day_index: int = Form(...), mood: str = Form("steady"), note: str = Form("")):
        ts = now_iso()
        with connect() as conn:
            conn.execute(
                """INSERT INTO daily_logs(plan_id,day_index,mood,note,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(plan_id,day_index) DO UPDATE SET mood=excluded.mood,note=excluded.note,updated_at=excluded.updated_at""",
                (plan_id, day_index, mood, note.strip(), ts, ts),
            )
            conn.commit()
        flash(request, "今日一句已经保存。", "success")
        return RedirectResponse(f"{request.url_for('daily_page')}?day={day_index}", status_code=303)

    @router.post("/daily/advance", name="daily_advance")
    def daily_advance(request: Request, plan_id: int = Form(...), current_day: int = Form(...)):
        with connect() as conn:
            plan = conn.execute("SELECT * FROM study_plans WHERE id=?", (plan_id,)).fetchone()
            required = conn.execute(
                "SELECT COUNT(*) total,COALESCE(SUM(completed),0) done FROM daily_missions WHERE plan_id=? AND day_index=? AND optional=0",
                (plan_id, current_day),
            ).fetchone()
            if required["total"] and required["done"] < required["total"]:
                flash(request, "必做任务需要先提交成果，才能开启下一关。", "error")
                return RedirectResponse(f"{request.url_for('daily_page')}?day={current_day}", status_code=303)
            next_day = min(current_day + 1, int(plan["total_days"]))
            conn.execute("UPDATE study_plans SET current_day=?,updated_at=? WHERE id=?", (next_day, now_iso(), plan_id))
            if next_day > current_day:
                conn.execute("INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)", ("day_clear", 8, f"通关 Day {current_day}", now_iso()))
                transact(conn, "spirit_stone", 5, f"通关 Day {current_day}")
                queue_event(
                    conn,
                    "day_cleared",
                    {"plan_id": plan_id, "day_index": current_day},
                    event_uuid=f"day-clear-{plan_id}-{current_day}",
                )
            conn.commit()
        best_effort_sync()
        flash(request, "今日关卡已结算，宝箱中还有 5 灵石。", "success")
        return RedirectResponse(f"{request.url_for('daily_page')}?day={next_day}", status_code=303)

    @router.get("/plans", response_class=HTMLResponse, name="plans_page")
    def plans_page(request: Request, plan_id: int | None = None, day: int | None = None):
        with connect() as conn:
            plans = [dict(row) for row in conn.execute("SELECT * FROM study_plans ORDER BY status='active' DESC,updated_at DESC")]
            selected = conn.execute("SELECT * FROM study_plans WHERE id=?", (plan_id,)).fetchone() if plan_id else None
            if not selected and plans:
                selected = conn.execute("SELECT * FROM study_plans WHERE id=?", (plans[0]["id"],)).fetchone()
            missions, day_map, selected_day = [], [], 1
            if selected:
                selected_day = max(1, min(int(day or selected["current_day"]), int(selected["total_days"])))
                missions = [dict(row) for row in conn.execute(
                    """SELECT m.*,t.name track_name,q.title cultivation_title,
                              w.name workspace_name,p.title project_title
                       FROM daily_missions m
                       LEFT JOIN research_tracks t ON t.id=m.track_id
                       LEFT JOIN quests q ON q.id=m.quest_id
                       LEFT JOIN workspaces w ON w.id=m.workspace_id
                       LEFT JOIN research_projects p ON p.id=m.project_id
                       WHERE m.plan_id=? AND m.day_index=? ORDER BY m.optional,m.sort_order,m.id""",
                    (selected["id"], selected_day),
                )]
                day_map = [row["day_index"] for row in conn.execute("SELECT DISTINCT day_index FROM daily_missions WHERE plan_id=? ORDER BY day_index", (selected["id"],))]
            tracks = [dict(row) for row in conn.execute("SELECT id,name FROM research_tracks WHERE active=1 ORDER BY sort_order,id")]
            cultivation_tasks = [
                dict(row)
                for row in conn.execute("SELECT id,title FROM quests WHERE completed=0 ORDER BY difficulty DESC,updated_at DESC,id")
            ]
            workspaces = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,name,icon FROM workspaces WHERE active=1 ORDER BY sort_order,id"
                )
            ]
            projects = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,title FROM research_projects WHERE status='active' ORDER BY updated_at DESC,id DESC"
                )
            ]
            plan_health = _plan_health(conn, int(selected["id"])) if selected else None
            plan_prompt = build_plan_prompt(current_state(conn))
        return templates.TemplateResponse(
            request=request, name="plans.html",
            context=context(request, "plans", plans=plans, selected=dict(selected) if selected else None,
                            missions=missions, day_map=day_map, selected_day=selected_day, tracks=tracks,
                            cultivation_tasks=cultivation_tasks, workspaces=workspaces,
                            projects=projects, plan_health=plan_health, plan_prompt=plan_prompt),
        )

    @router.post("/plans/import", name="plan_import")
    def plan_import(request: Request, plan_text: str = Form("")):
        try:
            spec = parse_plan_text(plan_text)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return go(request, "plans_page")
        with connect() as conn:
            plan_id = _insert_plan(conn, spec, make_active=True)
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("plan_import", 0, f"导入学习计划：{spec.name}", now_iso()),
            )
            health = _plan_health(conn, plan_id)
            if 3 <= len(spec.days) <= 7 and not health["warnings"]:
                conn.execute(
                    """
                    UPDATE easter_eggs
                    SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                    WHERE egg_key='balanced_plan'
                    """,
                    (now_iso(),),
                )
            conn.commit()
        flash(
            request,
            f"已导入并启用“{spec.name}”，共 {len(spec.days)} 天"
            f"{f'，并新增 {len(spec.cultivation_tasks)} 项独立修炼任务' if spec.cultivation_tasks else ''}。"
            "正在进入 Day 1；经验值已按系统统一规则计算。",
            "success",
        )
        return RedirectResponse(f"{request.url_for('daily_page')}?day=1", status_code=303)

    @router.post("/plans/{plan_id}/activate", name="plan_activate")
    def plan_activate(request: Request, plan_id: int):
        with connect() as conn:
            if not _activate_plan(conn, plan_id):
                flash(request, "没有找到这份计划，当前计划未被改变。", "error")
                return go(request, "plans_page")
            conn.commit()
        flash(request, "已设为当前计划，继续上次所在的 Day。", "success")
        return RedirectResponse(f"{request.url_for('daily_page')}", status_code=303)

    @router.post("/plans/{plan_id}/day", name="plan_set_day")
    def plan_set_day(request: Request, plan_id: int, current_day: int = Form(...)):
        with connect() as conn:
            total = conn.execute("SELECT total_days FROM study_plans WHERE id=?", (plan_id,)).fetchone()
            value = max(1, min(current_day, int(total["total_days"]))) if total else 1
            conn.execute("UPDATE study_plans SET current_day=?,updated_at=? WHERE id=?", (value, now_iso(), plan_id))
            conn.commit()
        flash(request, f"已从 Day {value} 继续。", "success")
        return go(request, "daily_page")

    @router.post("/plans/{plan_id}/mission/{mission_id}/save", name="plan_mission_save")
    def plan_mission_save(
        request: Request, plan_id: int, mission_id: int, category: str = Form("重点"), title: str = Form(...),
        description: str = Form(""), deliverable: str = Form(""), duration_minutes: int = Form(30),
        optional: str = Form(""), track_id: str = Form(""), quest_id: str = Form(""),
        workspace_id: str = Form(""), project_id: str = Form(""),
    ):
        track_value = int(track_id) if track_id.isdigit() else None
        quest_value = int(quest_id) if quest_id.isdigit() else None
        workspace_value = int(workspace_id) if workspace_id.isdigit() else None
        project_value = int(project_id) if project_id.isdigit() else None
        duration_value = max(5, min(int(duration_minutes or 30), 240))
        with connect() as conn:
            row = conn.execute("SELECT day_index FROM daily_missions WHERE id=? AND plan_id=?", (mission_id, plan_id)).fetchone()
            if track_value is None:
                track_value = _infer_track_id(conn, category, title, description)
            conn.execute(
                """UPDATE daily_missions
                   SET category=?,title=?,description=?,deliverable=?,duration_minutes=?,xp=?,
                       optional=?,track_id=?,quest_id=?,workspace_id=?,project_id=?,updated_at=?
                   WHERE id=? AND plan_id=?""",
                (
                    category.strip() or "重点",
                    title.strip(),
                    description.strip(),
                    deliverable.strip(),
                    duration_value,
                    fixed_daily_xp(duration_value),
                    int(optional == "1"),
                    track_value,
                    quest_value,
                    workspace_value,
                    project_value,
                    now_iso(),
                    mission_id,
                    plan_id,
                ),
            )
            conn.commit()
        flash(request, "任务已修改。", "success")
        selected_day = row["day_index"] if row else 1
        return RedirectResponse(f"{request.url_for('plans_page')}?plan_id={plan_id}&day={selected_day}", status_code=303)

    @router.post("/plans/{plan_id}/missions/new", name="plan_mission_new")
    def plan_mission_new(
        request: Request,
        plan_id: int,
        day_index: int = Form(1),
        category: str = Form("重点"),
        title: str = Form(...),
        description: str = Form(""),
        deliverable: str = Form(""),
        duration_minutes: int = Form(30),
        optional: str = Form(""),
        workspace_id: str = Form(""),
        project_id: str = Form(""),
    ):
        task_title = title.strip()[:240]
        if not task_title:
            flash(request, "任务名称不能为空。", "error")
            return RedirectResponse(
                f"{request.url_for('plans_page')}?plan_id={plan_id}&day={day_index}",
                status_code=303,
            )
        duration = max(5, min(int(duration_minutes or 30), 240))
        with connect() as conn:
            plan = conn.execute(
                "SELECT total_days FROM study_plans WHERE id=?",
                (plan_id,),
            ).fetchone()
            if not plan:
                raise HTTPException(status_code=404)
            selected_day = max(1, min(int(day_index), int(plan["total_days"])))
            order = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sort_order),-1)+1 n FROM daily_missions WHERE plan_id=? AND day_index=?",
                    (plan_id, selected_day),
                ).fetchone()["n"]
            )
            track_id = _infer_track_id(conn, category, task_title, description)
            ts = now_iso()
            conn.execute(
                """
                INSERT INTO daily_missions(
                    plan_id,day_index,category,title,description,deliverable,duration_minutes,xp,
                    optional,track_id,workspace_id,project_id,sort_order,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    plan_id,
                    selected_day,
                    category.strip() or "重点",
                    task_title,
                    description.strip()[:3000],
                    deliverable.strip()[:1200],
                    duration,
                    fixed_daily_xp(duration),
                    int(optional == "1"),
                    track_id,
                    int(workspace_id) if workspace_id.isdigit() else None,
                    int(project_id) if project_id.isdigit() else None,
                    order,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE study_plans SET updated_at=? WHERE id=?",
                (ts, plan_id),
            )
            conn.commit()
        flash(request, f"任务已加入 Day {selected_day}。", "success")
        return RedirectResponse(
            f"{request.url_for('plans_page')}?plan_id={plan_id}&day={selected_day}",
            status_code=303,
        )

    @router.post("/plans/{plan_id}/delete", name="plan_delete")
    def plan_delete(request: Request, plan_id: int):
        with connect() as conn:
            row = conn.execute("SELECT status FROM study_plans WHERE id=?", (plan_id,)).fetchone()
            if row and row["status"] == "active":
                flash(request, "当前计划不能直接删除，请先切换。", "error")
                return RedirectResponse(f"{request.url_for('plans_page')}?plan_id={plan_id}", status_code=303)
            conn.execute("DELETE FROM study_plans WHERE id=?", (plan_id,))
            conn.commit()
        flash(request, "计划已删除。", "success")
        return go(request, "plans_page")

    @router.get("/plans/{plan_id}/export", name="plan_export")
    def plan_export(plan_id: int):
        from fastapi.responses import PlainTextResponse
        with connect() as conn:
            row = conn.execute("SELECT source_text FROM study_plans WHERE id=?", (plan_id,)).fetchone()
        return PlainTextResponse(row["source_text"] if row else "", media_type="text/markdown; charset=utf-8")

    app.include_router(router)
