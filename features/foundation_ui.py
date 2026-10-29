from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.database import connect


def register_foundation_ui_routes(app, templates, context: Callable[..., dict[str, Any]]):
    router = APIRouter()

    @router.get('/foundation/upload-folder', response_class=HTMLResponse, name='foundation_folder_upload_page')
    def foundation_folder_upload_page(request: Request, track_id: int | None = None):
        with connect() as conn:
            tracks = [dict(row) for row in conn.execute('SELECT id,name FROM research_tracks WHERE active=1 ORDER BY sort_order,id')]
        return templates.TemplateResponse(
            request=request,
            name='folder_upload.html',
            context=context(request, 'folders', tracks=tracks, selected_track_id=track_id),
        )

    @router.get('/foundation/track/{track_id}', response_class=HTMLResponse, name='foundation_track_page')
    def foundation_track_page(request: Request, track_id: int):
        with connect() as conn:
            track = conn.execute('SELECT * FROM research_tracks WHERE id=?', (track_id,)).fetchone()
            tasks = [dict(row) for row in conn.execute(
                'SELECT * FROM research_plan_items WHERE track_id=? ORDER BY status="done",sort_order,id', (track_id,)
            )]
            folders = [dict(row) for row in conn.execute(
                'SELECT * FROM research_folders WHERE track_id=? ORDER BY updated_at DESC', (track_id,)
            )]
            tracks = [dict(row) for row in conn.execute('SELECT id,name FROM research_tracks WHERE active=1 ORDER BY sort_order,id')]
        if not track:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        total = len(tasks)
        done = sum(1 for task in tasks if task['status'] == 'done')
        return templates.TemplateResponse(
            request=request,
            name='foundation_track.html',
            context=context(
                request,
                'foundation',
                track=dict(track),
                tasks=tasks,
                folders=folders,
                tracks=tracks,
                progress=round(done / total * 100) if total else 0,
            ),
        )

    app.include_router(router)
