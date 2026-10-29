# ruff: noqa: F401
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import bleach

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse

from core.database import DB_PATH, connect, get_setting, init_db, log_activity, now_iso, set_setting, total_xp
from core.navigation import DEFAULT_NAV_LAYOUT, DEFAULT_NAV_LABELS, normalize_nav_layout, normalize_nav_labels
from services.content_library import EXTRA_POEMS
from services.extractors import extract_file
from services.research_tools import find_lammps_files, offline_paper_summary, parse_lammps_log, summary_to_markdown, unpack_lammps_bundle
from services.economy import balances as asset_balances, transact as asset_transact
from services.game_world import equipped_artifact
from services.backups import register_backup_jobs
from services.ai_provider import provider_status
from services.file_storage import MAX_UPLOAD_BYTES
from services.profile_media import PROFILE_DIR, current_avatar_filename
from services.idle_space import WALLPAPER_DIR
from services.review_engine import pending_review_group
from services.progression import (
    CULTIVATION_DIFFICULTY_LABELS,
    REALM_INDEX,
    REALM_STAGES,
    default_realm_labels,
    fixed_cultivation_xp,
    normalize_realm_labels,
    realm_state,
)
from core.runtime_paths import (
    APP_ROOT,
    DATA_ROOT,
    DISTRIBUTION_ROOT,
    INSTANCE_DIR,
    STORAGE_ROOT,
    USER_CONFIG_DIR,
)
from core.version import APP_VERSION, is_newer_version

BASE_DIR = APP_ROOT
UPLOAD_DIR = STORAGE_ROOT / "uploads"
BACKUP_DIR = STORAGE_ROOT / "backups"
SIMULATION_DIR = STORAGE_ROOT / "simulations"
FOUNDATION_DIR = STORAGE_ROOT / "research_foundation"
DELIVERY_DIR = STORAGE_ROOT / "deliveries"
NOTE_IMAGE_DIR = STORAGE_ROOT / "note_images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
SIMULATION_DIR.mkdir(parents=True, exist_ok=True)


def _session_secret() -> str:
    configured = os.environ.get("RESEARCH_OS_SECRET", "").strip()
    if configured:
        return configured
    secret_path = INSTANCE_DIR / "web_session_secret.txt"
    if secret_path.is_file():
        stored = secret_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    generated = secrets.token_urlsafe(48)
    secret_path.write_text(generated, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return generated
FOUNDATION_DIR.mkdir(parents=True, exist_ok=True)
DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
NOTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

KINDS = {
    "document": ("文献/文档", "📚"),
    "note": ("科研笔记", "📝"),
    "question": ("科学问题", "❓"),
    "experiment": ("实验记录", "🧪"),
    "dataset": ("数据集", "📊"),
    "image": ("图片/图谱", "🖼️"),
    "code": ("代码/模拟", "💻"),
    "sop": ("SOP/方法", "🧭"),
    "failure": ("失败复盘", "🔥"),
    "idea": ("灵感假设", "💡"),
    "other": ("其他", "📦"),
}

NAV_GROUP_LABEL_KEYS = {
    "cultivation": "group_cultivation",
    "knowledge": "group_knowledge",
    "workspaces": "group_workspaces",
    "growth": "group_growth",
    "research": "group_research",
    "system": "group_system",
}

# Decorative surfaces can be hidden by the user, but they are part of the full
# feature catalog by default. Practice (trials) is not in this set because it
# drives spaced review and remains independent of the gamification switch.
GAMIFICATION_ITEMS = {"achievements", "alchemy", "world", "idle"}


def gamification_enabled() -> bool:
    return get_setting("gamification", "on") == "on"


NAV_ITEM_DEFINITIONS = {
    "dashboard": {"endpoint": "dashboard", "icon": "⌂"},
    "cultivation": {"endpoint": "cultivation_page", "icon": "境"},
    "daily": {"endpoint": "daily_page", "icon": "始"},
    "review": {"endpoint": "review_page", "icon": "温"},
    "plans": {"endpoint": "plans_page", "icon": "近"},
    "projects": {"endpoint": "projects_page", "icon": "题"},
    "experiments": {"endpoint": "experiments_page", "icon": "验"},
    "simulations": {"endpoint": "simulations_page", "icon": "算"},
    "datasets": {"endpoint": "datasets_page", "icon": "数"},
    "retreat": {"endpoint": "retreat_page", "icon": "静"},
    "idle": {"endpoint": "idle_page", "icon": "守"},
    "literature_report": {"endpoint": "literature_report_page", "icon": "阅"},
    "workbench": {"endpoint": "workbench_page", "icon": "工"},
    "library": {"endpoint": "library", "icon": "藏"},
    "search": {"endpoint": "search", "icon": "寻"},
    "note_new": {"endpoint": "note_new", "icon": "记"},
    "upload": {"endpoint": "upload", "icon": "收"},
    "folders": {"endpoint": "folders_page", "icon": "夹"},
    "discover": {"endpoint": "discover_page", "icon": "网"},
    "workspace_shortcuts": {
        "kind": "workspace_shortcuts",
        "icon": "域",
        "editor_label": "启用的工作区入口（整体）",
    },
    "workspaces": {"endpoint": "workspaces_page", "icon": "＋"},
    "career": {"endpoint": "career_page", "icon": "程"},
    "trials": {"endpoint": "trials_page", "icon": "境"},
    "achievements": {"endpoint": "achievements_page", "icon": "章"},
    "alchemy": {"endpoint": "alchemy_page", "icon": "丹"},
    "world": {"endpoint": "world_page", "icon": "府"},
    "profile": {"endpoint": "profile_page", "icon": "我"},
    "assistant": {"endpoint": "assistant_page", "icon": "问"},
    "online": {"endpoint": "online_page", "icon": "联"},
    "settings": {"endpoint": "settings_page", "icon": "设"},
}



def secure_filename(name: str) -> str:
    base = Path(name).name
    safe = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._")
    return safe[:180] or "file"

DEFAULT_POEMS = [
    "纸上得来终觉浅，绝知此事要躬行。——陆游",
    "问渠那得清如许？为有源头活水来。——朱熹",
    "路漫漫其修远兮，吾将上下而求索。——屈原",
    "不畏浮云遮望眼，自缘身在最高层。——王安石",
    "博观而约取，厚积而薄发。——苏轼",
    "欲穷千里目，更上一层楼。——王之涣",
    "长风破浪会有时，直挂云帆济沧海。——李白",
    "沉舟侧畔千帆过，病树前头万木春。——刘禹锡",
    "山重水复疑无路，柳暗花明又一村。——陆游",
    "千淘万漉虽辛苦，吹尽狂沙始到金。——刘禹锡",
    "会当凌绝顶，一览众山小。——杜甫",
    "不识庐山真面目，只缘身在此山中。——苏轼",
    "操千曲而后晓声，观千剑而后识器。——刘勰",
    "业精于勤，荒于嬉；行成于思，毁于随。——韩愈",
    "锲而不舍，金石可镂。——荀子",
    "知之者不如好之者，好之者不如乐之者。——《论语》",
    "学而不思则罔，思而不学则殆。——《论语》",
    "温故而知新，可以为师矣。——《论语》",
    "知不足，然后能自反也。——《礼记》",
    "合抱之木，生于毫末；九层之台，起于累土。——《道德经》",
    "天下难事，必作于易；天下大事，必作于细。——《道德经》",
    "胜人者有力，自胜者强。——《道德经》",
    "行远自迩，登高自卑。——《礼记》",
    "不积跬步，无以至千里；不积小流，无以成江海。——荀子",
    "吾生也有涯，而知也无涯。——庄子",
    "试玉要烧三日满，辨材须待七年期。——白居易",
    "读书破万卷，下笔如有神。——杜甫",
    "少年辛苦终身事，莫向光阴惰寸功。——杜荀鹤",
    "及时当勉励，岁月不待人。——陶渊明",
    "宝剑锋从磨砺出，梅花香自苦寒来。——《警世贤文》",
    "莫愁前路无知己，天下谁人不识君。——高适",
] + EXTRA_POEMS

app = FastAPI(title="科研系统", docs_url=None, redoc_url=None)


@app.get("/api/app-identity", include_in_schema=False)
def app_identity():
    """Identify this local service without exposing the raw data path."""
    data_identity = hashlib.sha256(
        str(DATA_ROOT).casefold().encode("utf-8")
    ).hexdigest()
    return {
        "application": "wendao-research",
        "app_version": APP_VERSION,
        "data_root_hash": data_identity,
        "pid": os.getpid(),
    }
class SameOriginWriteGuard:
    """同源写保护：浏览器发起的跨站写请求一律 403。

    个人端没有登录态，Cookie 的 SameSite 防线不适用；
    恶意网页可以向 127.0.0.1 的写接口发起跨站 POST。本中间件在
    应用层统一拒绝带 Origin 且与 Host 不一致的写请求。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in {"POST", "PUT", "DELETE", "PATCH"}:
            headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
            origin = headers.get("origin")
            if origin:
                from urllib.parse import urlparse

                if urlparse(origin).netloc != headers.get("host", ""):
                    response = PlainTextResponse("跨站写请求已拒绝。", status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


app.add_middleware(SameOriginWriteGuard)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    same_site="lax",
    https_only=os.getenv("RESEARCH_OS_HTTPS_ONLY", "").strip() == "1",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/media/note-images", StaticFiles(directory=NOTE_IMAGE_DIR), name="note_images")
app.mount("/media/profile", StaticFiles(directory=PROFILE_DIR), name="profile_media")
app.mount("/media/wallpapers", StaticFiles(directory=WALLPAPER_DIR), name="idle_wallpapers")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def filesize_filter(size: int | None) -> str:
    size = int(size or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def date_filter(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


templates.env.filters["filesize"] = filesize_filter
templates.env.filters["datecn"] = date_filter


def configured_realm_names() -> dict[str, str]:
    try:
        custom_names = json.loads(get_setting("realm_names", "{}"))
    except json.JSONDecodeError:
        custom_names = {}
    return normalize_realm_labels(custom_names)


def configured_poem_pool() -> list[str]:
    try:
        custom = json.loads(get_setting("ui_poem_pool", "[]"))
    except json.JSONDecodeError:
        custom = []
    if isinstance(custom, list):
        cleaned = [str(item).strip()[:120] for item in custom if str(item).strip()]
        if cleaned:
            return cleaned[:366]
    legacy = get_setting("ui_home_poem", DEFAULT_POEMS[0]).strip()[:120]
    if legacy and legacy != DEFAULT_POEMS[0]:
        return [legacy, *DEFAULT_POEMS]
    return list(DEFAULT_POEMS)


def daily_poem(day: date | None = None) -> str:
    pool = configured_poem_pool()
    selected_day = day or date.today()
    return pool[selected_day.toordinal() % len(pool)]


def passed_tribulation_keys(conn=None) -> set[str]:
    owns = conn is None
    conn = conn or connect()
    try:
        return {
            str(row["gate_key"])
            for row in conn.execute(
                "SELECT DISTINCT gate_key FROM realm_tribulations WHERE status='passed'"
            )
        }
    finally:
        if owns:
            conn.close()


def current_realm(
    xp: int,
    passed_gates: set[str] | None = None,
) -> dict[str, Any]:
    return realm_state(
        xp,
        configured_realm_names(),
        passed_tribulation_keys() if passed_gates is None else passed_gates,
    )


def domains() -> list[str]:
    try:
        return json.loads(get_setting("domains", "[]"))
    except json.JSONDecodeError:
        return ["未分类"]


def update_badge_version() -> str:
    """Cached newer version for the sidebar dot; empty when nothing applies."""
    raw = get_setting("github_update", "")
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    version = str(payload.get("version", "")).strip()
    if not version:
        return ""
    return version if is_newer_version(version, APP_VERSION) else ""


def navigation_labels() -> dict[str, str]:
    try:
        custom = json.loads(get_setting("nav_labels", "{}"))
    except json.JSONDecodeError:
        custom = {}
    return normalize_nav_labels(custom)


def navigation_layout() -> list[dict[str, Any]]:
    try:
        custom = json.loads(get_setting("nav_layout", "[]"))
    except json.JSONDecodeError:
        custom = []
    return normalize_nav_layout(custom)


def navigation_sections(
    labels: dict[str, str],
    workspaces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for group in navigation_layout():
        items: list[dict[str, Any]] = []
        for saved_item in group["items"]:
            if not saved_item["visible"]:
                continue
            key = saved_item["key"]
            if key in GAMIFICATION_ITEMS and not gamification_enabled():
                continue
            definition = NAV_ITEM_DEFINITIONS[key]
            if key == "workspace_shortcuts" and not workspaces:
                continue
            item = {"key": key, **definition}
            if key != "workspace_shortcuts":
                item["label"] = labels[key]
            items.append(item)
        if items:
            sections.append(
                {
                    "key": group["key"],
                    "label": labels[NAV_GROUP_LABEL_KEYS[group["key"]]],
                    "items": items,
                }
            )
    return sections


def navigation_editor_layout() -> list[dict[str, Any]]:
    labels = navigation_labels()
    default_group_order = {
        group["key"]: index for index, group in enumerate(DEFAULT_NAV_LAYOUT)
    }
    default_item_order = {
        group["key"]: {
            item["key"]: index for index, item in enumerate(group["items"])
        }
        for group in DEFAULT_NAV_LAYOUT
    }
    editor: list[dict[str, Any]] = []
    for group in navigation_layout():
        group_key = group["key"]
        items = []
        for item in group["items"]:
            definition = NAV_ITEM_DEFINITIONS[item["key"]]
            items.append(
                {
                    **item,
                    "icon": definition["icon"],
                    "label": definition.get(
                        "editor_label",
                        labels.get(item["key"], item["key"]),
                    ),
                    "default_order": default_item_order[group_key][item["key"]],
                }
            )
        editor.append(
            {
                "key": group_key,
                "label": labels[NAV_GROUP_LABEL_KEYS[group_key]],
                "items": items,
                "default_order": default_group_order[group_key],
            }
        )
    return editor


def infer_kind(filename: str, selected: str) -> str:
    if selected and selected != "auto":
        return selected
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}:
        return "image"
    if suffix in {".csv", ".xlsx", ".xlsm", ".json", ".parquet", ".tsv", ".sav", ".h5", ".hdf5"}:
        return "dataset"
    if suffix in {".py", ".ipynb", ".r", ".m", ".cpp", ".c", ".h", ".java", ".sh", ".bat", ".yaml", ".yml"}:
        return "code"
    return "document"


def xp_for_kind(kind: str) -> int:
    return {
        "document": 12,
        "note": 10,
        "question": 15,
        "experiment": 18,
        "dataset": 25,
        "image": 8,
        "code": 16,
        "sop": 22,
        "failure": 20,
        "idea": 12,
        "other": 6,
    }.get(kind, 8)


def parse_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def entry_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["schema"] = parse_json(item.get("dataset_schema", "[]"), [])
    item["analysis"] = parse_json(item.get("analysis_json", "{}"), {})
    item["preview"] = parse_json(item.get("dataset_preview", "[]"), [])
    item["kind_label"], item["kind_icon"] = KINDS.get(item["kind"], KINDS["other"])
    item["tags_list"] = [x.strip() for x in re.split(r"[,，;；#]+", item.get("tags", "")) if x.strip()]
    return item


def activity_streak(conn) -> int:
    rows = conn.execute("SELECT DISTINCT substr(created_at, 1, 10) AS day FROM activities ORDER BY day DESC").fetchall()
    days = {row["day"] for row in rows}
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def achievements(conn) -> list[dict[str, Any]]:
    counts = {
        row["kind"]: int(row["n"])
        for row in conn.execute("SELECT kind, COUNT(*) n FROM entries GROUP BY kind")
    }
    total = sum(counts.values())
    domain_count = int(
        conn.execute(
            "SELECT COUNT(DISTINCT domain) n FROM entries WHERE domain != '未分类'"
        ).fetchone()["n"]
    )
    tagged = int(
        conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE trim(tags) != ''"
        ).fetchone()["n"]
    )

    def count(sql: str, params: tuple[Any, ...] = ()) -> int:
        return int(conn.execute(sql, params).fetchone()["n"])

    metrics = {
        "plans": count("SELECT COUNT(*) n FROM study_plans"),
        "missions": count(
            "SELECT COUNT(*) n FROM daily_missions WHERE completed=1"
        ),
        "deliveries": count("SELECT COUNT(*) n FROM mission_deliveries"),
        "review_sources": count("SELECT COUNT(*) n FROM review_sources"),
        "review_sessions": count(
            "SELECT COUNT(*) n FROM review_sessions WHERE status='completed'"
        ),
        "projects": count("SELECT COUNT(*) n FROM research_projects"),
        "multi_workspace_projects": count(
            """
            SELECT COUNT(*) n FROM (
                SELECT project_id FROM project_workspaces
                GROUP BY project_id HAVING COUNT(*)>=2
            )
            """
        ),
        "passed_gates": count(
            "SELECT COUNT(*) n FROM project_milestones WHERE status='passed'"
        ),
        "project_updates": count("SELECT COUNT(*) n FROM project_updates"),
        "career_moments": count("SELECT COUNT(*) n FROM career_moments"),
        "domains": count("SELECT COUNT(*) n FROM workspaces WHERE active=1"),
        "artifacts": count(
            "SELECT COUNT(*) n FROM inventory_items WHERE item_type='artifact'"
        ),
        "eggs": count("SELECT COUNT(*) n FROM easter_eggs WHERE unlocked=1"),
        "streak": activity_streak(conn),
        "xp": total_xp(conn),
    }

    def badge(
        name: str,
        desc: str,
        icon: str,
        category: str,
        current: int,
        target: int,
        tier: str = "青铜",
    ) -> dict[str, Any]:
        current = max(0, int(current))
        target = max(1, int(target))
        return {
            "name": name,
            "desc": desc,
            "icon": icon,
            "category": category,
            "tier": tier,
            "current": current,
            "target": target,
            "progress": min(100, round(current / target * 100)),
            "unlocked": current >= target,
        }

    return [
        badge("初入仙途", "建立第一条科研资产", "芽", "起步", total, 1),
        badge("藏经成卷", "收录10份文献或文档", "卷", "积累", counts.get("document", 0), 10),
        badge("问题猎手", "记录10个科学问题", "问", "思考", counts.get("question", 0), 10),
        badge(
            "百炼成钢",
            "完成10次实验或失败复盘",
            "炼",
            "实践",
            counts.get("experiment", 0) + counts.get("failure", 0),
            10,
            "白银",
        ),
        badge("数据炼丹师", "建立5个数据集档案", "数", "实践", counts.get("dataset", 0), 5),
        badge("法门传承者", "沉淀5份SOP", "法", "方法", counts.get("sop", 0), 5),
        badge("贯通诸域", "覆盖6个研究领域", "域", "积累", domain_count, 6, "黄金"),
        badge("秩序建立者", "为50条资料添加标签", "序", "积累", tagged, 50, "白银"),
        badge("三策成篇", "建立3份可替换的近期计划", "策", "行动", metrics["plans"], 3),
        badge("七步有痕", "完成7项每日任务", "步", "行动", metrics["missions"], 7),
        badge("三十次交付", "留下30份可核验交付", "证", "行动", metrics["deliveries"], 30, "黄金"),
        badge("十简成卷", "沉淀10份复盘关键文本", "忆", "复盘", metrics["review_sources"], 10, "白银"),
        badge("温故知新", "完成10次复盘或秘境", "温", "复盘", metrics["review_sessions"], 10, "白银"),
        badge("三题并进", "建立3个真实课题", "题", "课题", metrics["projects"], 3),
        badge(
            "诸域同参",
            "让一个课题关联至少两个工作区",
            "联",
            "课题",
            metrics["multi_workspace_projects"],
            1,
            "白银",
        ),
        badge("证据破关", "通过3个课题证据闸门", "闸", "课题", metrics["passed_gates"], 3, "黄金"),
        badge("推进有据", "写下10条改变判断的推进记录", "进", "课题", metrics["project_updates"], 10, "白银"),
        badge("生涯见证者", "记录5个重要生涯节点", "程", "生涯", metrics["career_moments"], 5, "白银"),
        badge("开域拓土", "同时维持5个学习领域", "域", "领域", metrics["domains"], 5, "白银"),
        badge("百器归心", "收集5件各有用途的法器", "器", "趣味", metrics["artifacts"], 5, "白银"),
        badge("彩蛋寻踪", "发现10枚隐藏彩蛋", "彩", "趣味", metrics["eggs"], 10, "黄金"),
        badge("七日连修", "连续7天留下真实行动", "日", "坚持", metrics["streak"], 7),
        badge("月轮不息", "连续30天留下真实行动", "月", "坚持", metrics["streak"], 30, "传说"),
        badge("千修成林", "累计获得1000修为", "千", "成长", metrics["xp"], 1000, "黄金"),
        badge("文献启封", "收录第一份文献或文档", "启", "起步", counts.get("document", 0), 1),
        badge("十问成锋", "记录10个可追踪科学问题", "锋", "思考", counts.get("question", 0), 10, "白银"),
        badge("百问观澜", "累计记录100个科学问题", "澜", "思考", counts.get("question", 0), 100, "传说"),
        badge("灵感成匣", "保存20条灵感假设", "灵", "思考", counts.get("idea", 0), 20, "白银"),
        badge("实验初火", "留下第一条实验记录", "火", "实践", counts.get("experiment", 0), 1),
        badge("实验百炼", "留下50条实验记录", "验", "实践", counts.get("experiment", 0), 50, "黄金"),
        badge("败而有方", "沉淀5次失败复盘", "败", "方法", counts.get("failure", 0), 5, "白银"),
        badge("十法归档", "沉淀10份可复用SOP", "法", "方法", counts.get("sop", 0), 10, "黄金"),
        badge("代码留痕", "保存10份代码或模拟资产", "码", "方法", counts.get("code", 0), 10, "白银"),
        badge("图谱成林", "收录20张图片或图谱", "图", "积累", counts.get("image", 0), 20, "白银"),
        badge("百卷藏经", "收录100份文献或文档", "百", "积累", counts.get("document", 0), 100, "传说"),
        badge("五库成阵", "建立5个结构化数据集", "库", "实践", counts.get("dataset", 0), 5, "白银"),
        badge("十策轮转", "建立10份短周期计划", "轮", "行动", metrics["plans"], 10, "黄金"),
        badge("初心三步", "完成3项每日任务", "三", "行动", metrics["missions"], 3),
        badge("百步成径", "完成100项每日任务", "径", "行动", metrics["missions"], 100, "传说"),
        badge("交付开印", "留下第一份可核验交付", "印", "行动", metrics["deliveries"], 1),
        badge("百证成链", "留下100份可核验交付", "链", "行动", metrics["deliveries"], 100, "传说"),
        badge("复盘初醒", "完成第一次复盘或秘境", "醒", "复盘", metrics["review_sessions"], 1),
        badge("五十回响", "完成50次复盘或秘境", "响", "复盘", metrics["review_sessions"], 50, "黄金"),
        badge("一题立案", "建立第一个真实课题", "立", "课题", metrics["projects"], 1),
        badge("十闸皆证", "通过10个课题证据闸门", "证", "课题", metrics["passed_gates"], 10, "传说"),
        badge("百日问道", "连续100天留下真实行动", "恒", "坚持", metrics["streak"], 100, "传说"),
    ]


def plain_excerpt(value: str | None, limit: int = 220) -> str:
    text = bleach.clean(value or "", tags=[], strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，。；;：: ") + "…"


RICH_TAGS = ["p","br","strong","b","em","i","u","ul","ol","li","h2","h3","blockquote","a","img","code","pre","hr"]
RICH_ATTRS = {"a": ["href","title","target"], "img": ["src","alt","title"]}


def sanitize_rich_content(value: str) -> str:
    return bleach.clean(value or "", tags=RICH_TAGS, attributes=RICH_ATTRS, protocols=["http","https"], strip=True)


def current_day_phase(moment: datetime | None = None) -> str:
    hour = (moment or datetime.now()).hour
    if 5 <= hour < 8:
        return "dawn"
    if 8 <= hour < 16:
        return "day"
    if 16 <= hour < 20:
        return "sunset"
    return "night"


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flashes", []).append({"category": category, "message": message})


def context(request: Request, active_page: str, **extra: Any) -> dict[str, Any]:
    xp = total_xp()
    with connect() as _asset_conn:
        _balances = asset_balances(_asset_conn)
        _equipped_artifact = equipped_artifact(_asset_conn)
        _artifact_notice_count = 0
        if _equipped_artifact and _equipped_artifact["key"] == "echo_bell":
            _artifact_notice_count = int(
                _asset_conn.execute(
                    """
                    SELECT COUNT(*) n FROM review_answers
                    WHERE next_due IS NOT NULL AND next_due<=?
                    """,
                    (date.today().isoformat(),),
                ).fetchone()["n"]
            )
        _profile = _asset_conn.execute(
            "SELECT avatar_symbol FROM player_profile WHERE id=1"
        ).fetchone()
        _workspaces = [
            dict(row)
            for row in _asset_conn.execute(
                "SELECT id,name,icon,module,accent FROM workspaces WHERE active=1 ORDER BY sort_order,id"
            )
        ]
    _nav_labels = navigation_labels()
    base = {
        "request": request,
        "site_name": get_setting("site_name", "科研系统"),
        "researcher_name": get_setting("researcher_name", "修士"),
        "nav_xp": xp,
        "nav_realm": current_realm(xp),
        "nav_assets": _balances,
        "nav_show_currency": gamification_enabled(),
        "nav_artifact": _equipped_artifact,
        "artifact_notice_count": _artifact_notice_count,
        "kinds": KINDS,
        "domains": domains(),
        "current_year": datetime.now().year,
        "active_page": active_page,
        "flashes": request.session.pop("flashes", []),
        "ui_accent": get_setting("ui_accent", "terracotta"),
        "ui_density": get_setting("ui_density", "comfortable"),
        "ui_scene": get_setting("ui_scene", "warm"),
        "ui_motion": get_setting("ui_motion", "balanced"),
        "ui_geometry": get_setting("ui_geometry", "soft"),
        "ui_font_scale": get_setting("ui_font_scale", "normal"),
        "ui_home_effect": get_setting("ui_home_effect", "orbits"),
        "ui_home_motto": get_setting("ui_home_motto", "让科研更好玩一点"),
        "ui_home_poem": daily_poem(),
        "day_phase": current_day_phase(),
        "nav_labels": _nav_labels,
        "nav_sections": navigation_sections(_nav_labels, _workspaces),
        "nav_update_version": update_badge_version(),
        "nav_workspaces": _workspaces,
        "nav_avatar_symbol": (_profile["avatar_symbol"] if _profile else "道") or "道",
        "avatar_file": current_avatar_filename(),
        "hub_configured": bool(
            get_setting("sync_provider", "disabled") == "legacy_hub"
            and get_setting("hub_url", "").strip()
            and get_setting("hub_api_token", "").strip()
        ),
    }
    base.update(extra)
    return base


def redirect(name: str, request: Request, **path_params: Any) -> RedirectResponse:
    return RedirectResponse(url=request.url_for(name, **path_params), status_code=303)


def _number(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
