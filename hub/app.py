"""中心端应用装配：中间件、静态资源与路由注册。

路由按领域拆分在 `hub/routes/*`，各自持有 APIRouter；
本模块只负责把它们组装成一个 FastAPI 应用。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from hub.common import STATIC_DIR, register_route_names, security_headers
from services.hub_storage import AVATAR_DIR, ensure_dirs
from hub.routes import admin, api, auth, me, market, pages, social, system
from hub.db import hub_secret

app = FastAPI(title="科研系统同行会", docs_url=None, redoc_url=None)
HUB_HTTPS_ONLY = os.getenv("HUB_HTTPS_ONLY", "0").strip() == "1"
HUB_TRUST_PROXY = os.getenv("HUB_TRUST_PROXY", "0").strip() == "1"
app.add_middleware(SessionMiddleware, secret_key=hub_secret(), same_site="lax", https_only=HUB_HTTPS_ONLY)
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
