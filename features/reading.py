"""论文三遍读与汇报工作台。"""

from __future__ import annotations

from typing import Any, Callable

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services import pdf_sketch, reading_plan, reading_flow as flow

FIELDS = (
    "title", "authors", "venue", "year", "decision", "category", "context",
    "contribution", "one_liner", "correctness", "clarity", "method_note",
    "data_setup", "results", "weakness", "relevance", "pass3_note", "scenario",
)


def _load(work_id: int | None) -> dict[str, Any] | None:
    if not work_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM reading_works WHERE id=?", (work_id,)).fetchone()
    return dict(row) if row else None


def _list_works() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM reading_works ORDER BY updated_at DESC, id DESC"
        )]
    for row in rows:
        row["progress"] = flow.progress(row)
        row["stage_label"] = flow.stage_label(row["stage"])
    return rows


def register_reading_routes(app, templates, context: Callable[..., dict[str, Any]], flash):
    router = APIRouter()

    def page(request: Request, work_id: int = 0):
        works = _list_works()
        selected = _load(work_id) if work_id else (works[0] if works else None)
        if selected:
            selected["stage_label"] = flow.stage_label(selected["stage"])
        payload = {
            "works": works,
            "work": selected,
            "stages": flow.STAGES,
            "decisions": flow.DECISIONS,
            "scenarios": flow.SCENARIOS,
            "five_c": flow.FIVE_C,
            "qa": flow.qa_questions(selected) if selected else [],
        }
        if selected:
            try:
                payload["extract"] = json.loads(selected.get("extract_json") or "{}")
            except json.JSONDecodeError:
                payload["extract"] = {}
            payload.update(
                checklist=flow.checklist(selected),
                progress=flow.progress(selected),
                outline=flow.outline(selected),
                outline_text=flow.outline_text(selected),
                card_text=flow.literature_card(selected),
            )
        return templates.TemplateResponse(
            request=request, name="reading.html",
            context=context(request, "reading", **payload),
        )

    @router.get("/reading", response_class=HTMLResponse, name="reading_page")
    def reading_page(request: Request, work_id: int = 0):
        return page(request, work_id)

    @router.post("/reading/new", name="reading_new")
    def reading_new(request: Request, title: str = Form(...), venue: str = Form(""), year: str = Form("")):
        title = title.strip()[:200]
        if not title:
            flash(request, "先填论文标题。", "error")
            return RedirectResponse(request.url_for("reading_page"), status_code=303)
        ts = now_iso()
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO reading_works(title,authors,venue,year,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (title, "", venue.strip()[:120], year.strip()[:10], ts, ts),
            )
            conn.commit()
            work_id = int(cursor.lastrowid)
        flash(request, "已建档。先花 10 分钟做第一遍，回答五个 C。")
        return RedirectResponse(f"{request.url_for('reading_page')}?work_id={work_id}", status_code=303)

    @router.post("/reading/{work_id}/save", name="reading_save")
    async def reading_save(request: Request, work_id: int):
        form = await request.form()
        values = {name: str(form.get(name, "")).strip()[:2000] for name in FIELDS}
        if not values["title"]:
            flash(request, "标题不能为空。", "error")
            return RedirectResponse(request.url_for("reading_page"), status_code=303)
        assignments = ",".join(f"{name}=?" for name in values)
        with connect() as conn:
            conn.execute(
                f"UPDATE reading_works SET {assignments},updated_at=? WHERE id=?",
                (*[values[name] for name in FIELDS], now_iso(), work_id),
            )
            conn.commit()
        flash(request, "已保存。")
        return RedirectResponse(f"{request.url_for('reading_page')}?work_id={work_id}", status_code=303)

    @router.post("/reading/{work_id}/stage", name="reading_stage")
    def reading_stage(request: Request, work_id: int, stage: str = Form("")):
        work = _load(work_id)
        if not work:
            flash(request, "这篇记录不存在。", "error")
            return RedirectResponse(request.url_for("reading_page"), status_code=303)
        target = stage if stage in {key for key, _l, _h in flow.STAGES} else flow.next_stage(work["stage"])
        with connect() as conn:
            conn.execute("UPDATE reading_works SET stage=?,updated_at=? WHERE id=?", (target, now_iso(), work_id))
            conn.commit()
        flash(request, f"已推进到「{flow.stage_label(target)}」。")
        return RedirectResponse(f"{request.url_for('reading_page')}?work_id={work_id}", status_code=303)

    @router.get("/reading/plan", response_class=HTMLResponse, name="reading_plan_page")
    def reading_plan_page(request: Request, work_id: int = 0):
        """SPECS → 碳水泥阅读计划：顺序、任务、交付、统一记录与随手记。"""
        reading_plan.ensure_default_plan()
        plan_data = reading_plan.plan_payload()
        payload = {"plan_data": plan_data, "unified_edit": None,
                   "item_unified": plan_data["item_unified"]}
        if work_id:
            payload["unified_edit"] = reading_plan.load_unified(work_id)
        return templates.TemplateResponse(
            request=request, name="reading_plan.html",
            context=context(request, "reading_plan", **payload),
        )

    @router.post("/reading/plan/{item_id}/link", name="reading_plan_link")
    def reading_plan_link(request: Request, item_id: int, work_id: int = Form(0)):
        reading_plan.ensure_default_plan()
        with connect() as conn:
            item = conn.execute("SELECT * FROM reading_plan_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            flash(request, "计划条目不存在。", "error")
            return RedirectResponse(request.url_for("reading_plan_page"), status_code=303)
        if work_id:
            reading_plan.link_item(item_id, work_id)
            flash(request, "已关联到三遍读工作台中的论文。")
            return RedirectResponse(request.url_for("reading_plan_page"), status_code=303)
        # 未选论文：按条目标题自动建档
        ts = now_iso()
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO reading_works(title,venue,year,created_at,updated_at,plan_item_id)
                   VALUES (?,?,?,?,?,?)""",
                (str(item["label"]), "", "", ts, ts, item_id),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)
        reading_plan.link_item(item_id, new_id)
        flash(request, "已建档并关联；可在三遍读工作台按任务填写。")
        return RedirectResponse(f"{request.url_for('reading_plan_page')}?work_id={new_id}", status_code=303)

    @router.post("/reading/plan/{item_id}/status", name="reading_plan_status")
    def reading_plan_status(request: Request, item_id: int, status: str = Form("")):
        reading_plan.ensure_default_plan()
        if reading_plan.set_status(item_id, status):
            flash(request, f"状态已更新为 {status}。")
        else:
            flash(request, "状态值无效。", "error")
        return RedirectResponse(request.url_for("reading_plan_page"), status_code=303)

    @router.post("/reading/plan/unified/{work_id}", name="reading_plan_unified")
    async def reading_plan_unified(request: Request, work_id: int):
        reading_plan.ensure_default_plan()
        form = await request.form()
        values = {key: str(form.get(key, "")) for key, _hint in reading_plan.UNIFIED_FIELDS}
        filled = reading_plan.save_unified(work_id, values)
        flash(request, f"统一记录已保存（{filled}/5 栏）。")
        return RedirectResponse(f"{request.url_for('reading_plan_page')}?work_id={work_id}", status_code=303)

    @router.post("/reading/plan/notes", name="reading_plan_note")
    async def reading_plan_note(request: Request):
        reading_plan.ensure_default_plan()
        form = await request.form()
        work_id = int(form.get("work_id", 0) or 0)
        kind = str(form.get("kind", ""))
        content = str(form.get("content", ""))
        if work_id and reading_plan.add_note(work_id, kind, content):
            flash(request, "已随手记录。")
        else:
            flash(request, "先选择论文，再填写内容。", "error")
        return RedirectResponse(request.url_for("reading_plan_page"), status_code=303)

    @router.post("/reading/{work_id}/extract", name="reading_extract")
    async def reading_extract(request: Request, work_id: int, file: UploadFile = File(...)):
        """只抽标题/图表标题/结论，填不填由人决定。"""
        work = _load(work_id)
        if not work:
            flash(request, "这篇记录不存在。", "error")
            return RedirectResponse(request.url_for("reading_page"), status_code=303)
        try:
            result = pdf_sketch.sketch(await file.read())
        except ValueError as exc:
            flash(request, str(exc), "error")
        else:
            with connect() as conn:
                conn.execute(
                    "UPDATE reading_works SET extract_json=?,updated_at=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), now_iso(), work_id),
                )
                conn.commit()
            flash(request, f"已抽取：标题 1 条、图表标题 {len(result['captions'])} 条、结论 1 段。")
        return RedirectResponse(f"{request.url_for('reading_page')}?work_id={work_id}", status_code=303)

    @router.post("/reading/{work_id}/qa", name="reading_qa_save")
    async def reading_qa_save(request: Request, work_id: int):
        form = await request.form()
        work = _load(work_id)
        if not work:
            flash(request, "这篇记录不存在。", "error")
            return RedirectResponse(request.url_for("reading_page"), status_code=303)
        answers = {key: str(form.get(f"qa_{key}", "")) for key, _label, _q in flow.QA_TREE}
        with connect() as conn:
            conn.execute(
                "UPDATE reading_works SET qa_json=?,updated_at=? WHERE id=?",
                (flow.dump_qa(answers, flow.parse_qa(work["qa_json"])), now_iso(), work_id),
            )
            conn.commit()
        answered = sum(1 for value in answers.values() if value.strip())
        flash(request, f"已保存 {answered}/5 条问答准备。")
        return RedirectResponse(f"{request.url_for('reading_page')}?work_id={work_id}", status_code=303)

    @router.post("/reading/{work_id}/delete", name="reading_delete")
    def reading_delete(request: Request, work_id: int):
        with connect() as conn:
            conn.execute("DELETE FROM reading_works WHERE id=?", (work_id,))
            conn.commit()
        flash(request, "已删除这篇记录。")
        return RedirectResponse(request.url_for("reading_page"), status_code=303)

    app.include_router(router)
