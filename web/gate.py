"""可选访问门禁：给部署到公网的个人节点加一道口令锁。

设计
----
- 默认关闭：只有设置了 `RESEARCH_OS_ACCESS_CODE` 才启用，本地单机使用完全不受影响。
- 启用后，除 `/gate` 与 `/static/*` 外的所有路径（含挂载在 `/team` 的同行会）都要先过门禁。
- 通过校验后写入一个签名 Cookie（SHA-256(前缀 + 口令)，httponly）；
  口令本身不落库、不进会话，换口令即全体会话失效。
- 中间件是纯 ASGI 实现，不依赖会话中间件的包裹顺序。
- 口令错误时延迟 0.6 秒再返回，压低在线爆破的速率。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from html import escape
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

COOKIE_NAME = "research_os_gate"
_TOKEN_PREFIX = "research-os-gate-v1:"
_GATE_FAILURES: dict[str, deque[float]] = defaultdict(deque)


def access_code() -> str:
    return os.getenv("RESEARCH_OS_ACCESS_CODE", "").strip()


def gate_enabled() -> bool:
    return bool(access_code())


def gate_token(code: str) -> str:
    return hashlib.sha256((_TOKEN_PREFIX + code).encode("utf-8")).hexdigest()


def _safe_next(value: str | None) -> str:
    next_path = (value or "/").strip()
    if (
        not next_path.startswith("/")
        or next_path.startswith("//")
        or "\\" in next_path
        or any(char in next_path for char in ("\r", "\n", "\x00"))
    ):
        return "/"
    return next_path


def _gate_page(next_path: str, error: str = "", *, status_code: int = 200) -> HTMLResponse:
    next_value = escape(next_path, quote=True)
    message = f'<p class="gate-error">{escape(error)}</p>' if error else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>问道科研 · 访问验证</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(160deg,#20303f,#1b2836);color:#eee;font-family:"Microsoft YaHei",system-ui,sans-serif}}
form{{background:rgba(255,255,255,.06);padding:36px 32px;border-radius:14px;width:min(340px,88vw);box-shadow:0 12px 40px rgba(0,0,0,.35)}}
h1{{font-size:20px;margin:0 0 6px;letter-spacing:.06em}}
small{{color:#9fb0bf;display:block;margin-bottom:22px}}
input{{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:9px;border:1px solid rgba(255,255,255,.22);background:rgba(255,255,255,.08);color:#eee;font-size:14px}}
button{{width:100%;margin-top:16px;padding:11px;border:0;border-radius:9px;background:#3d8b6e;color:#fff;font-size:14px;cursor:pointer}}
.gate-error{{color:#f0a9a0;font-size:12px;margin:10px 0 0}}
.hint{{font-size:11px;color:#7d8e9c;margin-top:18px;line-height:1.7}}
</style></head><body>
<form method="post" action="/gate">
  <h1>问道科研</h1><small>本部署开启了访问验证</small>
  <input type="password" name="code" placeholder="访问口令" autofocus required>
  <input type="hidden" name="next" value="{next_value}">
  <button>进入</button>
  {message}
  <p class="hint">口令由部署者通过环境变量 RESEARCH_OS_ACCESS_CODE 设置。</p>
</form>
</body></html>"""
    return HTMLResponse(
        html,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        },
    )


def _gate_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _gate_allowed(ip: str) -> bool:
    now = time.time()
    failures = _GATE_FAILURES[ip]
    while failures and now - failures[0] > 600:
        failures.popleft()
    return len(failures) < 8


def _record_gate_failure(ip: str) -> None:
    _GATE_FAILURES[ip].append(time.time())


class AccessGateMiddleware:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/gate" or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        cookie = Request(scope).cookies.get(COOKIE_NAME, "")
        if cookie and secrets.compare_digest(cookie, self.token):
            await self.app(scope, receive, send)
            return
        next_path = quote(path or "/", safe="/")
        response = RedirectResponse(f"/gate?next={next_path}", status_code=303)
        await response(scope, receive, send)


def _register_gate_routes(app: FastAPI, token: str) -> None:
    @app.get("/gate", name="access_gate", include_in_schema=False)
    async def gate_page(request: Request):
        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie and secrets.compare_digest(cookie, token):
            return RedirectResponse("/", status_code=303)
        return _gate_page(_safe_next(request.query_params.get("next")))

    @app.post("/gate", name="access_gate_submit", include_in_schema=False)
    async def gate_submit(request: Request):
        form = await request.form()
        code = str(form.get("code", "")).strip()
        next_path = _safe_next(str(form.get("next", "/")))
        ip = _gate_ip(request)
        if not _gate_allowed(ip):
            return _gate_page(
                next_path,
                error="尝试次数过多，请10分钟后再试。",
                status_code=429,
            )
        if code and secrets.compare_digest(code, access_code()):
            _GATE_FAILURES.pop(ip, None)
            response = RedirectResponse(next_path, status_code=303)
            response.set_cookie(
                COOKIE_NAME,
                gate_token(code),
                max_age=30 * 24 * 3600,
                httponly=True,
                samesite="lax",
                secure=(
                    request.url.scheme == "https"
                    or os.getenv("RESEARCH_OS_HTTPS_ONLY", "").strip() == "1"
                ),
            )
            return response
        _record_gate_failure(ip)
        await asyncio.sleep(0.6)
        return _gate_page(next_path, error="口令不正确，请重试。")


def install_access_gate(app: FastAPI) -> bool:
    """按环境变量安装门禁；未设置时返回 False，本地单机行为完全不变。"""
    code = access_code()
    if not code:
        return False
    token = gate_token(code)
    app.add_middleware(AccessGateMiddleware, token=token)
    _register_gate_routes(app, token)
    return True
