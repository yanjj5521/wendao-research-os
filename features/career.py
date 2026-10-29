from __future__ import annotations

from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services.career import CAREER_PHASE_MAP, CAREER_PHASES, MOMENT_TYPES, career_snapshot


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _go(request: Request) -> RedirectResponse:
    return RedirectResponse(request.url_for("career_page"), status_code=303)


def register_career_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    @router.get("/career", response_class=HTMLResponse, name="career_page")
    def career_page(request: Request):
        with connect() as conn:
            snapshot = career_snapshot(conn)
            projects = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,title,status FROM research_projects ORDER BY status='active' DESC,updated_at DESC,id"
                )
            ]
        return templates.TemplateResponse(
            request=request,
            name="career.html",
            context=context(
                request,
                "career",
                career=snapshot,
                career_phases=CAREER_PHASES,
                moment_types=MOMENT_TYPES,
                projects=projects,
                today=date.today().isoformat(),
            ),
        )

    @router.post("/career/focus", name="career_focus_save")
    def career_focus_save(
        request: Request,
        phase: str = Form("foundation"),
        focus: str = Form(""),
        boundary: str = Form(""),
        success_signal: str = Form(""),
        review_date: str = Form(""),
    ):
        phase_key = phase if phase in CAREER_PHASE_MAP else "foundation"
        review = _clean(review_date, 10)
        if review:
            try:
                date.fromisoformat(review)
            except ValueError:
                flash(request, "复看日期格式无效。", "error")
                return _go(request)
        values = {
            "career_phase": phase_key,
            "career_focus": _clean(focus, 600),
            "career_boundary": _clean(boundary, 600),
            "career_success_signal": _clean(success_signal, 600),
            "career_review_date": review,
        }
        with connect() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings(key,value) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                (
                    "career_focus",
                    0,
                    f"更新科研阶段罗盘：{CAREER_PHASE_MAP[phase_key]['label']}",
                    now_iso(),
                ),
            )
            conn.commit()
        flash(request, "阶段罗盘已保存；它可以随研究变化，不是长期承诺。")
        return _go(request)

    @router.post("/career/moments/new", name="career_moment_new")
    def career_moment_new(
        request: Request,
        moment_type: str = Form("decision"),
        title: str = Form(""),
        summary: str = Form(""),
        evidence: str = Form(""),
        project_id: int = Form(0),
        occurred_on: str = Form(""),
    ):
        moment_key = moment_type if moment_type in MOMENT_TYPES else "decision"
        title_text = _clean(title, 180)
        if not title_text:
            flash(request, "请给这个生涯节点一个标题。", "error")
            return _go(request)
        occurred = _clean(occurred_on, 10) or date.today().isoformat()
        try:
            date.fromisoformat(occurred)
        except ValueError:
            flash(request, "节点日期格式无效。", "error")
            return _go(request)
        ts = now_iso()
        with connect() as conn:
            linked_project = int(project_id or 0)
            if linked_project and not conn.execute(
                "SELECT 1 FROM research_projects WHERE id=?", (linked_project,)
            ).fetchone():
                linked_project = 0
            conn.execute(
                """
                INSERT INTO career_moments(
                    moment_type,title,summary,evidence,project_id,occurred_on,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    moment_key,
                    title_text,
                    _clean(summary, 3000),
                    _clean(evidence, 3000),
                    linked_project or None,
                    occurred,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                (
                    "career_moment",
                    0,
                    f"记录生涯节点：{title_text}",
                    ts,
                ),
            )
            conn.execute(
                """
                UPDATE easter_eggs
                SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                WHERE egg_key='career_witness'
                """,
                (ts,),
            )
            conn.commit()
        flash(request, "这个节点已经进入科研生涯档案。")
        return _go(request)

    app.include_router(router)
