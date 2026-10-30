"""中心端共享层：会话、安全、上下文与审计。

只有这一层知道"如何鉴权、如何渲染"；`hub/routes/*` 只表达"提供什么页面与接口"。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from services.hub_storage import avatar_path
from hub.db import (
    HUB_ADMIN_PATH,
    balances,
    connect_hub,
    get_user_by_token,
    now_iso,
)
from core import accounts as accounts_repo
from core.navigation import normalize_nav_labels, normalize_nav_layout
from services.progression import normalize_realm_labels
from core.runtime_paths import APP_ROOT
from core.version import APP_VERSION

BASE_DIR = APP_ROOT
TEMPLATE_DIR = BASE_DIR / "hub_templates"
STATIC_DIR = BASE_DIR / "hub_static"
VERSION = APP_VERSION
HUB_TRUST_PROXY = os.getenv("HUB_TRUST_PROXY", "0").strip() == "1"

templates = Jinja2Templates(directory=TEMPLATE_DIR)

_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # A few legacy form handlers use absolute redirects.  When the hub is
    # mounted inside the main application, keep those redirects inside /team.
    root_path = request.scope.get("root_path", "").rstrip("/")
    location = response.headers.get("location", "")
    if root_path and location.startswith("/") and not location.startswith(root_path + "/"):
        response.headers["location"] = root_path + location
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    if request.url.path.startswith("/api/") or request.url.path in {"/login", "/register", "/me"}:
        response.headers["Cache-Control"] = "no-store"
    return response


def client_ip(request: Request) -> str:
    if HUB_TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def login_allowed(ip: str) -> bool:
    now = time.time()
    queue = _LOGIN_ATTEMPTS[ip]
    while queue and now - queue[0] > 600:
        queue.popleft()
    return len(queue) < 8


def record_login_failure(ip: str) -> None:
    _LOGIN_ATTEMPTS[ip].append(time.time())


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="请求校验失败，请刷新页面重试。")


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flashes", []).append({"message": message, "category": category})


def current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("hub_user_id")
    if not user_id:
        return None
    with connect_hub() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=? AND active=1", (int(user_id),)).fetchone()
        return dict(row) if row else None


def require_user(request: Request, admin: bool = False) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    if admin and user["role"] != "admin":
        raise HTTPException(status_code=403)
    return user


_ROUTE_NAME_TO_PATH: dict[str, str] = {}
_PATH_PARAM = __import__("re").compile(r"\{(\w+)(?::[^}]+)?\}")


def register_route_names(router: Any) -> None:
    """记录路由名到路径的映射。

    FastAPI 0.162 的 include_router 采用惰性挂载，`request.url_for(name)`
    无法解析被包含路由的名字，所以这里在装配时自建一张映射表作为兜底。
    """
    for route in getattr(router, "routes", []):
        name = getattr(route, "name", None)
        path = getattr(route, "path", None)
        if name and path and name not in _ROUTE_NAME_TO_PATH:
            _ROUTE_NAME_TO_PATH[name] = path


def hub_url_for(request: Request, name: str, **params: Any) -> str:
    """按注册表生成**相对**链接（以 origin 为基准）。

    相对链接对反向代理与 http/https 升级完全免疫：不依赖 uvicorn 是否
    信任 X-Forwarded-Proto，也不依赖 request.base_url 的 scheme。
    仅当名字不在注册表（理论上只发生在测试或装配顺序出错）时，
    才退回 request.url_for 的原生解析。
    """
    path = _ROUTE_NAME_TO_PATH.get(name)
    if path is not None:
        path_params = set(_PATH_PARAM.findall(path))
        missing = path_params - params.keys()
        if missing:
            raise RuntimeError(f"生成链接缺少参数：{name} 缺少 {sorted(missing)}")
        url = _PATH_PARAM.sub(lambda m: str(params[m.group(1)]), path)
        from urllib.parse import urlencode

        query = urlencode({k: v for k, v in params.items() if k not in path_params})
        root = request.scope.get("root_path", "").rstrip("/")
        return f"{root}{url}" + (f"?{query}" if query else "")
    for candidate in (f"team:{name}", name):
        try:
            return str(request.url_for(candidate, **params))
        except Exception:
            continue
    raise RuntimeError(f"同行会路由不存在：{name}")


def initial_admin_hint(request: Request) -> dict[str, str] | None:
    """Expose the one-time admin credential on the login page of the host only.

    The file is created once, is deleted as soon as the admin changes the
    password, and is never shown to remote clients.  The credential is also
    re-verified against the account table so a stale file cannot leak a
    password that no longer works.
    """
    host = (request.client.host if request.client else "") or ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return None
    if not HUB_ADMIN_PATH.exists():
        return None
    try:
        text = HUB_ADMIN_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    username = password = ""
    for line in text.splitlines():
        if line.startswith("用户名:"):
            username = line.split(":", 1)[1].strip()
        elif line.startswith("密码:"):
            password = line.split(":", 1)[1].strip()
    if not username or not password:
        return None
    with connect_hub() as conn:
        if not accounts_repo.authenticate(conn, username, password):
            return None
    return {"username": username, "password": password}


def hub_context(request: Request, active: str, **extra: Any) -> dict[str, Any]:
    user = current_user(request)
    base = {
        "request": request,
        "url_for": lambda name, **params: hub_url_for(request, name, **params),
        "user": user,
        "active": active,
        "csrf": csrf_token(request),
        "flashes": request.session.pop("flashes", []),
        "version": VERSION,
        "media_base": request.scope.get("root_path", "").rstrip("/"),
        "system_home": os.environ.get("RESEARCH_OS_SYSTEM_URL", "").strip()
        or ("/" if request.scope.get("root_path", "").rstrip("/") else ""),
        "site_name": "科研系统 · 同行会",
    }
    if user:
        with connect_hub() as conn:
            base["nav_balances"] = balances(conn, int(user["id"]))
    base.update(extra)
    return base


def api_user(request: Request) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少API Token")
    token = auth.split(" ", 1)[1].strip()
    with connect_hub() as conn:
        row = get_user_by_token(conn, token)
        if not row:
            raise HTTPException(status_code=401, detail="API Token无效")
        conn.execute("UPDATE accounts SET last_seen_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
        conn.commit()
        return dict(row)


def profile_payload(conn, user_id: int) -> dict[str, Any]:
    user = dict(conn.execute("SELECT id,username,display_name,role,updated_at,last_seen_at FROM accounts WHERE id=?", (user_id,)).fetchone())
    profile = dict(conn.execute("SELECT * FROM account_profiles WHERE account_id=?", (user_id,)).fetchone())
    avatar = avatar_path(int(user_id))
    profile["avatar_file"] = avatar.name if avatar else None
    try:
        theme = json.loads(profile.pop("theme_json") or "{}")
    except json.JSONDecodeError:
        theme = {}
    inventory = [dict(row) for row in conn.execute("SELECT item_key,item_type,quantity,level,equipped,updated_at FROM hub_inventory WHERE user_id=? ORDER BY item_type,item_key", (user_id,))]
    latest = conn.execute("SELECT version,title,notes,file_size,sha256,created_at FROM hub_releases WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    welfare = [
        dict(row)
        for row in conn.execute(
            """SELECT id,xp,spirit_stone,spirit_wood,mystic_iron,star_sand,reason,created_at
               FROM hub_welfare_grants WHERE recipient_id=? AND claimed_at IS NULL ORDER BY id ASC LIMIT 20""",
            (user_id,),
        )
    ]
    return {
        "user": user,
        "profile": profile,
        "theme": theme,
        "balances": balances(conn, user_id),
        "inventory": inventory,
        "latest_release": dict(latest) if latest else None,
        "welfare_grants": welfare,
        "hub_version": VERSION,
    }


def audit(conn, user_id: int | None, action: str, detail: str, request: Request | None = None) -> None:
    conn.execute(
        "INSERT INTO hub_audit_log(user_id,action,detail,ip,created_at) VALUES (?,?,?,?,?)",
        (user_id, action, detail[:500], client_ip(request) if request else "", now_iso()),
    )




def parse_theme(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            data = {}
    else:
        data = value or {}
    allowed = {
        "accent": {"terracotta", "amber", "sage", "ink"},
        "density": {"focus", "comfortable", "compact", "spacious", "ultra"},
        "scene": {"warm", "forest", "paper", "night"},
    }
    result = {
        "accent": data.get("accent", "terracotta"),
        "density": data.get("density", "comfortable"),
        "scene": data.get("scene", "warm"),
        "home_motto": str(data.get("home_motto", "让科研更好玩一点"))[:80],
        "home_poem": str(data.get("home_poem", "纸上得来终觉浅，绝知此事要躬行。——陆游"))[:120],
        "site_name": str(data.get("site_name", "科研系统"))[:60],
        "review_popup": "1" if str(data.get("review_popup", "1")) == "1" else "0",
    }
    result["realm_names"] = normalize_realm_labels(data.get("realm_names", {}))
    nav_labels = data.get("nav_labels", {})
    result["nav_labels"] = normalize_nav_labels(nav_labels)
    result["nav_layout"] = normalize_nav_layout(data.get("nav_layout", []))
    for key, values in allowed.items():
        if result[key] not in values:
            result[key] = next(iter(values))
    return result



