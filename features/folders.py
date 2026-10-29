from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.database import connect


def register_folder_routes(app, templates, context: Callable[..., dict[str, Any]]):
    router = APIRouter()

    @router.get('/folders', response_class=HTMLResponse, name='folders_page')
    def folders_page(request: Request):
        with connect() as conn:
            folders = [dict(row) for row in conn.execute(
                "SELECT f.*,t.name track_name FROM research_folders f LEFT JOIN research_tracks t ON t.id=f.track_id ORDER BY f.updated_at DESC"
            )]
        return templates.TemplateResponse(
            request=request,
            name='folders.html',
            context=context(request, 'folders', folders=folders),
        )

    app.include_router(router)
