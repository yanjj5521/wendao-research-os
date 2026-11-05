"""论文三遍读与汇报工作台。"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services import reading_flow as flow

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
