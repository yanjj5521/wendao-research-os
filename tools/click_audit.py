"""发布前点击级核验：个人端所有 GET 页面 + 中心端页面 + 关键操作。

用法（隔离数据目录）：
    RESEARCH_OS_DATA_DIR=<临时目录> python tools/click_audit.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("RESEARCH_OS_DETERMINISTIC", "1")
if not os.environ.get("RESEARCH_OS_DATA_DIR"):
    os.environ["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="click-audit-")

from fastapi.testclient import TestClient  # noqa: E402

ERROR_MARKERS = ("程序遇到内部错误", "页面出错了", "页面不存在", "服务器内部错误", "Traceback")

DOWNLOAD_PATHS = {
    "/backup",
    "/portable",
    "/knowledge/export",
    "/export/json",
    "/online/personalization/export",
    "/experiments/export.csv",
}


def crawl_personal() -> list[str]:
    from core.database import init_db, set_setting
    from web.app import app

    init_db()
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "tests" / "v3_route_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    paths = [
        item["path"]
        for item in manifest
        if item["method"] == "GET" and "{" not in item["path"]
    ]
    client = TestClient(app)
    problems: list[str] = []
    for path in paths:
        response = client.get(path, follow_redirects=False)
        if path in {"/foundation"}:
            # 旧入口有意 303 到近期计划
            if response.status_code not in (302, 303):
                problems.append(f"{path} 期望跳转，实际 {response.status_code}")
            continue
        if path == "/online/enter":
            if response.status_code not in (302, 303):
                problems.append(f"{path} 期望跳转，实际 {response.status_code}")
            continue
        if response.status_code != 200:
            problems.append(f"{path} 状态 {response.status_code}")
            continue
        if path in DOWNLOAD_PATHS:
            if not response.content:
                problems.append(f"{path} 返回空内容")
            continue
        text = response.text
        for marker in ERROR_MARKERS:
            if marker in text:
                problems.append(f"{path} 出现错误页标记：{marker}")
                break

    # /team 身份分流三分支
    set_setting("sync_provider", "disabled")
    set_setting("hub_url", "")
    set_setting("hub_api_token", "")
    unbound = client.get("/team/")
    if unbound.status_code != 200 or "选择你的同行会身份" not in unbound.text:
        problems.append("/team 未选择身份时未给出身份选择页")

    set_setting("sync_provider", "legacy_hub")
    set_setting("hub_url", "http://127.0.0.1:5050")
    set_setting("hub_api_token", "self")
    host = client.get("/team/")
    if host.status_code != 200 or "选择你的同行会身份" in host.text:
        problems.append("/team 中心身份时未进入同伴会应用")
    return problems


def crawl_hub() -> list[str]:
    from hub.app import app
    from hub.db import HUB_ADMIN_PATH, init_hub_db

    init_hub_db()
    client = TestClient(app, base_url="http://hubserver", root_path="")
    password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
    page = client.get("/")
    csrf = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
    login = client.post(
        "/login",
        data={"username": "admin", "password": password, "_csrf": csrf},
        follow_redirects=False,
    )
    problems: list[str] = []
    if login.status_code != 303:
        problems.append(f"中心登录失败：{login.status_code}")
        return problems
    for path in ("/", "/members", "/resources", "/releases", "/me", "/admin", "/forum", "/chat", "/market", "/register"):
        response = client.get(path, follow_redirects=False)
        if response.status_code not in (200, 303):
            problems.append(f"hub {path} 状态 {response.status_code}")
            continue
        if response.status_code == 200:
            for marker in ERROR_MARKERS:
                if marker in response.text:
                    problems.append(f"hub {path} 出现错误页标记：{marker}")
                    break
            for link in _same_origin_links(response.text):
                target = client.get(link, follow_redirects=False)
                if target.status_code in (404, 500):
                    problems.append(f"hub {path} 里的链接 {link} 返回 {target.status_code}")
    return problems


def exercise_actions() -> list[str]:
    """关键操作走一遍：写笔记、改设置、换主题、结契、炼丹、买法器、切职业。"""
    from fastapi.testclient import TestClient

    from core.database import connect
    from services.economy import transact
    from services.review_engine import add_or_increment_herb
    from web.app import app

    client = TestClient(app)
    problems: list[str] = []

    note = client.post(
        "/notes/new",
        data={"title": "点击核验笔记", "kind": "note", "domain": "未分类", "content": "内容"},
        follow_redirects=False,
    )
    if note.status_code != 303:
        problems.append(f"写笔记失败：{note.status_code}")
    else:
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM entries WHERE title='点击核验笔记' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            problems.append("写笔记后没有生成条目")
        else:
            detail = client.get(f"/entry/{row['id']}")
            if detail.status_code != 200:
                problems.append(f"条目详情打不开：{detail.status_code}")

    settings = client.post(
        "/settings",
        data={
            "site_name": "问道科研",
            "researcher_name": "修士",
            "domains": "未分类",
            "ai_mode": "offline",
            "ai_endpoint": "http://127.0.0.1:11434/api/generate",
            "ai_model": "qwen2.5:7b",
            "review_popup": "1",
            "gamification": "on",
            "github_auto_check": "1",
            "ui_accent": "terracotta",
            "ui_density": "comfortable",
            "ui_scene": "warm",
            "ui_motion": "balanced",
            "ui_geometry": "soft",
            "ui_font_scale": "normal",
            "ui_home_effect": "orbits",
            "ui_home_motto": "把学习变成可以积累的事",
        },
        follow_redirects=False,
    )
    if settings.status_code != 303:
        problems.append(f"保存设置失败：{settings.status_code}")

    theme = client.post(
        "/online/theme",
        data={
            "accent": "terracotta",
            "density": "comfortable",
            "scene": "warm",
            "motion": "balanced",
            "geometry": "soft",
            "font_scale": "normal",
            "home_effect": "orbits",
            "home_motto": "把学习变成可以积累的事",
            "home_poem": "纸上得来终觉浅",
        },
        follow_redirects=False,
    )
    if theme.status_code != 303:
        problems.append(f"保存个性化失败：{theme.status_code}")

    adopt = client.post("/pets/adopt", data={"species_id": "dewfrog", "nickname": "小露"})
    if adopt.status_code != 200 or "灵兽" not in adopt.text:
        problems.append(f"灵兽结契失败：{adopt.status_code}")

    with connect() as conn:
        add_or_increment_herb(conn, 1, 5)
        transact(conn, "spirit_stone", 60, "点击核验充值")
        conn.commit()

    craft = client.post("/alchemy/craft/clarity_pill")
    if craft.status_code != 200 or ("炼制成功" not in craft.text and "炸炉" not in craft.text):
        problems.append(f"炼丹失败：{craft.status_code}")

    buy = client.post("/world/artifacts/qingxin_slip/buy", follow_redirects=False)
    if buy.status_code != 303:
        problems.append(f"购买法器失败：{buy.status_code}")

    switch = client.post("/professions/artificer/activate", follow_redirects=False)
    if switch.status_code != 303:
        problems.append(f"切换主修失败：{switch.status_code}")
    return problems


LINK_PAGE_CANDIDATES = (
    "/", "/daily", "/plans", "/review", "/projects", "/experiments", "/datasets",
    "/simulations", "/integral", "/retreat", "/idle", "/workbench", "/library",
    "/search", "/notes/new", "/upload", "/folders", "/discover", "/workspaces",
    "/career", "/trials", "/achievements", "/professions", "/alchemy", "/world",
    "/pets", "/profile", "/assistant", "/online", "/settings", "/cultivation",
)


def _same_origin_links(text: str) -> set[str]:
    links: set[str] = set()
    for match in re.finditer(r'(?:href|action)="([^"]+)"', text):
        value = match.group(1).strip()
        if not value.startswith("/") or value.startswith("//"):
            continue
        if value.startswith(("/static/", "/media/")):
            continue
        links.add(value.split("#")[0] or "/")
    return links


def crawl_links() -> list[str]:
    """从每个页面抽出站内链接与表单 action，逐个点一遍，确认不落 404/500。"""
    from fastapi.testclient import TestClient

    from web.app import app

    client = TestClient(app)
    problems: list[str] = []
    seen: set[str] = set()
    queue = list(LINK_PAGE_CANDIDATES)
    while queue:
        page = queue.pop(0)
        response = client.get(page, follow_redirects=False)
        if response.status_code != 200 or "text/html" not in response.headers.get("content-type", ""):
            continue
        for link in _same_origin_links(response.text):
            if link in seen:
                continue
            seen.add(link)
            target = client.get(link, follow_redirects=False)
            if target.status_code in (404, 500):
                problems.append(f"{page} 里的链接 {link} 返回 {target.status_code}")
    return problems


def main() -> int:
    problems = crawl_personal() + crawl_hub() + exercise_actions() + crawl_links()
    if problems:
        print("发现问题：")
        for item in problems:
            print(" -", item)
        return 1
    print("全部页面可达，关键操作可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
