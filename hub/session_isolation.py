"""ASGI helper that keeps an embedded app's session cookie isolated.

Starlette's SessionMiddleware stores its state in ``scope["session"]``.  When a
Hub app with its own SessionMiddleware is mounted inside the personal app, the
inner middleware replaces the outer personal session.  This tiny wrapper
restores the outer session before the outer middleware serializes its response,
while still allowing the inner middleware to emit its own ``hub_session``
cookie.
"""
from __future__ import annotations

from typing import Any


class PreserveOuterSession:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        marker = object()
        outer_session = scope.get("session", marker)
        restored = False

        async def send_wrapper(message: dict) -> None:
            nonlocal restored
            if not restored and message.get("type") == "http.response.start":
                restored = True
                if outer_session is marker:
                    scope.pop("session", None)
                else:
                    scope["session"] = outer_session
            await send(message)

        await self.app(scope, receive, send_wrapper)


def preserve_outer_session(app: Any) -> PreserveOuterSession:
    return PreserveOuterSession(app)
