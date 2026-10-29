from __future__ import annotations

from typing import Any


DEFAULT_NAV_LABELS = {
    "dashboard": "主页",
    "daily": "今日学习",
    "cultivation": "学习记录",
    "review": "温故知新",
    "plans": "近期计划",
    "projects": "项目",
    "experiments": "实验台账",
    "simulations": "模拟案例",
    "datasets": "数据集",
    "career": "成长罗盘",
    "retreat": "闭关计时",
    "idle": "挂机静修",
    "literature_report": "文献精读",
    "workbench": "学习工作台",
    "trials": "秘境试炼",
    "achievements": "成就图鉴",
    "alchemy": "炼丹炉",
    "world": "我的洞府",
    "profile": "个人主页",
    "assistant": "AI 协作",
    "online": "同行会",
    "library": "知识库",
    "folders": "交付文件夹",
    "note_new": "写笔记",
    "upload": "上传资料",
    "search": "全库检索",
    "discover": "联网找论文",
    "workspaces": "学习领域管理",
    "settings": "设置与备份",
    "group_cultivation": "学习节奏",
    "group_knowledge": "知识积累",
    "group_workspaces": "学习领域",
    "group_growth": "成长与陪伴",
    "group_research": "研究与输出",
    "group_system": "系统",
    "start": "记录进展",
    "knowledge_export": "一键导出知识库",
    "backup": "完整备份",
}

# Every implemented personal-workflow surface is present here. Visibility is a
# user preference, not a permanent product decision: the full set is shown for
# new installs and can be collapsed again from 设置 → 调整导航.
NAV_GROUP_DEFAULT_ITEMS = {
    "cultivation": (
        "dashboard", "daily", "plans", "review", "workbench", "cultivation", "retreat", "idle",
    ),
    "research": ("literature_report", "projects", "experiments", "datasets", "simulations"),
    "knowledge": (
        "library", "search", "note_new", "upload", "folders", "discover",
    ),
    "workspaces": ("workspace_shortcuts", "workspaces"),
    "growth": (
        "career", "profile", "trials", "achievements", "alchemy", "world",
    ),
    "system": ("assistant", "online", "settings"),
}

NAV_ITEM_DEFAULT_VISIBLE = {
    key: True
    for key in (
        "dashboard",
        "daily",
        "plans",
        "review",
        "workbench",
        "cultivation",
        "retreat",
        "idle",
        "literature_report",
        "projects",
        "experiments",
        "datasets",
        "simulations",
        "library",
        "search",
        "note_new",
        "upload",
        "folders",
        "discover",
        "workspace_shortcuts",
        "workspaces",
        "career",
        "profile",
        "trials",
        "achievements",
        "alchemy",
        "world",
        "assistant",
        "online",
        "settings",
    )
}

DEFAULT_NAV_LAYOUT = [
    {
        "key": group_key,
        "items": [
            {"key": item_key, "visible": NAV_ITEM_DEFAULT_VISIBLE.get(item_key, False)}
            for item_key in item_keys
        ],
    }
    for group_key, item_keys in NAV_GROUP_DEFAULT_ITEMS.items()
]

# The 1.0-1.3 focused layout. It is kept only to distinguish an untouched
# factory layout from an intentional user customization during upgrades.
_PREVIOUS_NAV_GROUP_ITEMS = {
    "cultivation": (
        "dashboard", "daily", "plans", "review", "workbench", "cultivation", "retreat", "idle",
    ),
    "research": ("literature_report", "projects"),
    "knowledge": (
        "library", "search", "note_new", "upload", "folders", "discover",
    ),
    "workspaces": ("workspace_shortcuts", "workspaces"),
    "growth": (
        "career", "profile", "trials", "achievements", "alchemy", "world",
    ),
    "system": ("assistant", "online", "settings"),
}
_PREVIOUS_NAV_VISIBLE = {
    "dashboard",
    "daily",
    "plans",
    "review",
    "literature_report",
    "projects",
    "library",
    "workspaces",
    "settings",
}
PREVIOUS_DEFAULT_NAV_LAYOUT = [
    {
        "key": group_key,
        "items": [
            {"key": item_key, "visible": item_key in _PREVIOUS_NAV_VISIBLE}
            for item_key in item_keys
        ],
    }
    for group_key, item_keys in _PREVIOUS_NAV_GROUP_ITEMS.items()
]


def normalize_nav_labels(value: Any) -> dict[str, str]:
    labels = dict(DEFAULT_NAV_LABELS)
    if isinstance(value, dict):
        for key in DEFAULT_NAV_LABELS:
            candidate = str(value.get(key, "")).strip()[:24]
            if candidate:
                labels[key] = candidate
    return labels


def _nav_visible(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "off", "no", "hidden"}
    if value is None:
        return False
    return bool(value)


def _nav_layout_signature(value: Any) -> tuple[tuple[str, tuple[tuple[str, bool], ...]], ...] | None:
    if not isinstance(value, list):
        return None
    signature: list[tuple[str, tuple[tuple[str, bool], ...]]] = []
    for raw_group in value:
        if not isinstance(raw_group, dict):
            return None
        group_key = str(raw_group.get("key", "")).strip()
        raw_items = raw_group.get("items", [])
        if not group_key or not isinstance(raw_items, list):
            return None
        items: list[tuple[str, bool]] = []
        for raw_item in raw_items:
            if isinstance(raw_item, str):
                item_key = raw_item.strip()
                visible = True
            elif isinstance(raw_item, dict):
                item_key = str(raw_item.get("key", "")).strip()
                visible = _nav_visible(raw_item.get("visible", True))
            else:
                return None
            if item_key:
                items.append((item_key, visible))
        signature.append((group_key, tuple(items)))
    return tuple(signature)


def is_previous_default_nav_layout(value: Any) -> bool:
    """Return True only for the untouched focused factory layout."""
    return _nav_layout_signature(value) == _nav_layout_signature(PREVIOUS_DEFAULT_NAV_LAYOUT)


def normalize_nav_layout(value: Any) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    seen_groups: set[str] = set()

    for raw_group in source:
        if not isinstance(raw_group, dict):
            continue
        group_key = str(raw_group.get("key", "")).strip()
        if group_key not in NAV_GROUP_DEFAULT_ITEMS or group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        allowed_items = NAV_GROUP_DEFAULT_ITEMS[group_key]
        seen_items: set[str] = set()
        items: list[dict[str, Any]] = []
        raw_items = raw_group.get("items", [])
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if isinstance(raw_item, str):
                    item_key = raw_item.strip()
                    visible = True
                elif isinstance(raw_item, dict):
                    item_key = str(raw_item.get("key", "")).strip()
                    visible = _nav_visible(raw_item.get("visible", True))
                else:
                    continue
                if item_key not in allowed_items or item_key in seen_items:
                    continue
                seen_items.add(item_key)
                items.append({"key": item_key, "visible": visible})
        for item_key in allowed_items:
            if item_key not in seen_items:
                items.append({"key": item_key, "visible": NAV_ITEM_DEFAULT_VISIBLE.get(item_key, False)})
        normalized.append({"key": group_key, "items": items})

    for group_key, item_keys in NAV_GROUP_DEFAULT_ITEMS.items():
        if group_key not in seen_groups:
            normalized.append(
                {
                    "key": group_key,
                    "items": [
                        {"key": item_key, "visible": NAV_ITEM_DEFAULT_VISIBLE.get(item_key, False)}
                        for item_key in item_keys
                    ],
                }
            )
    return normalized