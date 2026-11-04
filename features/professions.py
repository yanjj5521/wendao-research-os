"""修仙百艺页面：三职业、等阶与加成。"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect
from services import professions


def register_profession_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    def _stock() -> dict[str, int]:
        with connect() as conn:
            herbs = int(
                conn.execute("SELECT COALESCE(SUM(quantity),0) n FROM herb_inventory").fetchone()["n"]
            )
            artifacts = int(
                conn.execute(
                    "SELECT COUNT(*) n FROM inventory_items WHERE item_type='artifact'"
                ).fetchone()["n"]
            )
            pills = int(
                conn.execute(
                    "SELECT COALESCE(SUM(quantity),0) n FROM inventory_items WHERE item_type='pill'"
                ).fetchone()["n"]
            )
        return {"herbs": herbs, "artifacts": artifacts, "pills": pills}

    @router.get("/professions", response_class=HTMLResponse, name="professions_page")
    def professions_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="professions.html",
            context=context(
                request,
                "professions",
                arts=professions.state(),
                stock=_stock(),
            ),
        )

    @router.post("/professions/{profession_key}/activate", name="professions_activate")
    def professions_activate(request: Request, profession_key: str):
        try:
            professions.set_active(profession_key)
        except ValueError:
            flash(request, "未知职业。", "error")
        else:
            flash(
                request,
                f"主修已切换为 {professions.PROFESSIONS[profession_key]['name']}。"
                "主修只影响展示重点，三个职业的熟练度都会继续累积。",
            )
        return RedirectResponse(request.url_for("professions_page"), status_code=303)

    app.include_router(router)
