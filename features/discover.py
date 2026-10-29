from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from services.scholar_search import ScholarSearchError, search_works

def register_discover_routes(app, templates, context: Callable[..., dict[str, Any]]):
    router = APIRouter()

    @router.get('/discover', response_class=HTMLResponse, name='discover_page')
    def discover_page(request: Request, q: str = ''):
        results: list[dict[str, Any]] = []
        error = ''
        query = q.strip()
        if query:
            try:
                results = search_works(query, limit=8, timeout=8)
            except (ScholarSearchError, ValueError) as exc:
                error = f'联网检索暂时不可用：{exc}'
        return templates.TemplateResponse(
            request=request,
            name='discover.html',
            context=context(request, 'discover', q=query, results=results, error=error),
        )

    app.include_router(router)
