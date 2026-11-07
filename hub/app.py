"""中心端应用装配：中间件、静态资源与路由注册。

路由按领域拆分在 `hub/routes/*`，各自持有 APIRouter；
本模块只负责把它们组装成一个 FastAPI 应用。
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from hub.common import (
    STATIC_DIR,
    current_user,
    flash,
    hub_context,
    register_route_names,
    security_headers,
    templates,
)
from services.hub_storage import AVATAR_DIR, ensure_dirs
from hub.routes import admin, api, auth, me, market, pages, social, system
from hub.db import hub_secret

app = FastAPI(title="科研系统同行会", docs_url=None, redoc_url=None)
HUB_HTTPS_ONLY = os.getenv("HUB_HTTPS_ONLY", "0").strip() == "1"
HUB_TRUST_PROXY = os.getenv("HUB_TRUST_PROXY", "0").strip() == "1"
app.add_middleware(
    SessionMiddleware,
    secret_key=hub_secret(),
    session_cookie="hub_session",
    same_site="lax",
    https_only=HUB_HTTPS_ONLY,
)
_allowed_hosts = [x.strip() for x in os.getenv("HUB_ALLOWED_HOSTS", "").split(",") if x.strip()]
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)
app.middleware("http")(security_headers)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
ensure_dirs()
app.mount("/media/avatars", StaticFiles(directory=AVATAR_DIR), name="hub_avatars")

for router_module in (system, pages, auth, social, market, me, admin, api):
    app.include_router(router_module.router)
    register_route_names(router_module.router)


@app.exception_handler(Exception)
async def hub_unhandled_error(request: Request, exc: Exception):
    """未捕获异常也走友好页：多用户并发时，一个成员的报错不该吓到其他成员。"""
    import traceback as _tb

    _tb.print_exception(type(exc), exc, exc.__traceback__)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "服务器内部错误"}, status_code=500)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context=hub_context(
            request,
            "error",
            status_code=500,
            title="程序遇到内部错误",
            message="已记录错误；你的资料不受影响。可返回首页继续，或稍后重试。",
        ),
        status_code=500,
    )


@app.exception_handler(StarletteHTTPException)
async def hub_http_error(request: Request, exc: StarletteHTTPException):
    """Keep HTML users out of raw JSON error pages.

    Expired or foreign session cookies used to raise 401/403 and render a blank
    JSON body.  Guests are now sent back to the login page with a flash; signed
    in users get a page with explicit return actions.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code in (401, 403) and current_user(request) is None:
        flash(request, "登录状态已失效，请重新登录后再试。", "error")
        return RedirectResponse("/", status_code=303)
    if 400 <= exc.status_code < 500:
        titles = {
            400: "请求无法处理",
            401: "登录状态已失效",
            403: "没有访问权限",
            404: "页面不存在",
            405: "请求方式不被支持",
        }
        messages = {
            400: "这个请求缺少必要信息，请返回后重试。",
            401: "请重新登录后再访问这个页面。",
            403: "当前账号没有访问这个页面的权限，或页面校验已过期。",
            404: "你访问的页面不存在，可能已被移除或链接有误。",
            405: "这个地址不接受当前操作，请从页面内的按钮重新进入。",
        }
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=hub_context(
                request,
                "error",
                status_code=exc.status_code,
                title=titles.get(exc.status_code, "请求未完成"),
                message=messages.get(exc.status_code, "请返回后重试。"),
            ),
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
