"""灵知的经验、等级与形态。

Pure logic only: the desktop window supplies a daily snapshot, this module
decides XP awards, level-ups and which form is unlocked.  State is a plain dict
so it can live inside the existing ``desktop_pet.json`` without a migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

MAX_LEVEL = 20
WAKE_XP = 10
ACTIVITY_XP = 2
ACTIVITY_CAP = 40
MISSION_XP = 8
MISSION_CAP = 24
PAT_XP = 1
PAT_CAP = 5

FORMS: tuple[dict[str, Any], ...] = (
    {
        "key": "sprout",
        "name": "幼芽",
        "min_level": 1,
        "tint": (110, 170, 120),
        "alpha": 0.0,
        "scale": 1.0,
        "note": "刚醒来的样子，安静、透明。",
    },
    {
        "key": "leaf",
        "name": "青叶",
        "min_level": 3,
        "tint": (72, 148, 112),
        "alpha": 0.08,
        "scale": 1.02,
        "note": "叶脉清晰，摇起来更有弹性。",
    },
    {
        "key": "flower",
        "name": "灵花",
        "min_level": 5,
        "tint": (208, 158, 88),
        "alpha": 0.10,
        "scale": 1.04,
        "note": "边缘泛起暖金色，像把光收在叶尖。",
    },
    {
        "key": "fruit",
        "name": "道果",
        "min_level": 8,
        "tint": (196, 118, 76),
        "alpha": 0.12,
        "scale": 1.06,
        "note": "颜色沉下来，动作也更稳。",
    },
    {
        "key": "star",
        "name": "星灵",
        "min_level": 12,
        "tint": (118, 168, 208),
        "alpha": 0.10,
        "scale": 1.05,
        "note": "带一点冷光，像夜里值班的小灯。",
    },
)


def xp_for_level(level: int) -> int:
    """Cumulative XP needed to *be* this level. Lv1 = 0, Lv2 = 50 ..."""
    level = max(1, min(MAX_LEVEL, int(level)))
    return 25 * (level - 1) * level


def level_for_xp(xp: int) -> int:
    level = 1
    while level < MAX_LEVEL and xp >= xp_for_level(level + 1):
        level += 1
    return level


def form_for_level(level: int) -> dict[str, Any]:
    unlocked = [form for form in FORMS if form["min_level"] <= level]
    return unlocked[-1] if unlocked else FORMS[0]


def next_form(level: int) -> dict[str, Any] | None:
    for form in FORMS:
        if form["min_level"] > level:
            return form
    return None


@dataclass
class Progress:
    xp: int = 0
    level: int = 1
    form: str = "sprout"
    day: str = ""
    today: dict[str, Any] = field(default_factory=dict)
    seen_activity_id: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "xp": int(self.xp),
            "level": int(self.level),
            "form": self.form,
            "day": self.day,
            "today": dict(self.today),
            "seen_activity_id": int(self.seen_activity_id),
        }


def _fresh_today() -> dict[str, Any]:
    return {
        "wake": False,
        "activity_xp": 0,
        "mission_xp": 0,
        "pat_xp": 0,
        "seen_missions": 0,
    }


def load_progress(raw: Any, today: str | None = None) -> Progress:
    day = today or date.today().isoformat()
    data = raw if isinstance(raw, dict) else {}
    progress = Progress(
        xp=max(0, int(data.get("xp", 0) or 0)),
        day=str(data.get("day") or ""),
        seen_activity_id=max(0, int(data.get("seen_activity_id", 0) or 0)),
    )
    today_state = data.get("today") if isinstance(data.get("today"), dict) else {}
    progress.today = {**_fresh_today(), **today_state}
    if progress.day != day:
        progress.day = day
        progress.today = _fresh_today()
    progress.level = level_for_xp(progress.xp)
    progress.form = form_for_level(progress.level)["key"]
    return progress


def _award_xp(progress: Progress, amount: int, reason: str) -> None:
    if amount <= 0:
        return
    before_level = progress.level
    before_form = progress.form
    progress.xp += int(amount)
    progress.level = level_for_xp(progress.xp)
    progress.form = form_for_level(progress.level)["key"]
    if progress.level > before_level:
        unlocked = form_for_level(progress.level)
        progress.events.append(
            {
                "type": "level",
                "level": progress.level,
                "form": unlocked["key"],
                "form_name": unlocked["name"],
                "unlocked": unlocked["key"] != before_form,
                "reason": reason,
            }
        )
    else:
        progress.events.append(
            {"type": "xp", "amount": int(amount), "reason": reason}
        )


def award(
    progress: Progress,
    *,
    day: str,
    activity_ids: list[int] | None = None,
    missions_done: int = 0,
    bootstrap: bool = False,
) -> tuple[Progress, list[dict[str, Any]]]:
    """Apply daily wake/activity/mission XP idempotently.

    ``bootstrap`` is used the first time this version runs: existing history is
    marked as seen so a long-time user does not jump several levels at once.
    """
    progress.events = []
    if progress.day != day:
        progress.day = day
        progress.today = _fresh_today()
    ids = sorted(int(value) for value in (activity_ids or []))
    if bootstrap:
        progress.seen_activity_id = max(ids) if ids else progress.seen_activity_id
        progress.today["seen_missions"] = max(0, int(missions_done))
    if not progress.today.get("wake"):
        progress.today["wake"] = True
        _award_xp(progress, WAKE_XP, "每日首次唤醒")
    new_ids = [value for value in ids if value > progress.seen_activity_id]
    if new_ids:
        room = max(0, ACTIVITY_CAP - int(progress.today.get("activity_xp", 0)))
        gain = min(len(new_ids) * ACTIVITY_XP, room)
        if gain:
            progress.today["activity_xp"] = int(progress.today.get("activity_xp", 0)) + gain
            _award_xp(progress, gain, f"新增 {len(new_ids)} 条学习活动")
        progress.seen_activity_id = max(ids)
    seen_missions = max(0, int(progress.today.get("seen_missions", 0)))
    delta = max(0, int(missions_done) - seen_missions)
    if delta:
        room = max(0, MISSION_CAP - int(progress.today.get("mission_xp", 0)))
        gain = min(delta * MISSION_XP, room)
        if gain:
            progress.today["mission_xp"] = int(progress.today.get("mission_xp", 0)) + gain
            _award_xp(progress, gain, f"完成 {delta} 项今日必做")
        progress.today["seen_missions"] = int(missions_done)
    return progress, list(progress.events)


def record_pat(progress: Progress, *, day: str) -> tuple[Progress, list[dict[str, Any]]]:
    progress.events = []
    if progress.day != day:
        progress.day = day
        progress.today = _fresh_today()
    if int(progress.today.get("pat_xp", 0)) < PAT_CAP:
        progress.today["pat_xp"] = int(progress.today.get("pat_xp", 0)) + PAT_XP
        _award_xp(progress, PAT_XP, "摸摸头")
    return progress, list(progress.events)


def describe(progress: Progress) -> dict[str, Any]:
    current = xp_for_level(progress.level)
    following = xp_for_level(progress.level + 1) if progress.level < MAX_LEVEL else None
    span = (following - current) if following else 1
    percent = 1.0 if following is None else max(0.0, min(1.0, (progress.xp - current) / span))
    form = form_for_level(progress.level)
    upcoming = next_form(progress.level)
    return {
        "level": progress.level,
        "xp": progress.xp,
        "level_start": current,
        "next_level_xp": following,
        "percent": percent,
        "form": form,
        "next_form": upcoming,
        "today": dict(progress.today),
        "max_level": progress.level >= MAX_LEVEL,
    }


def rules_text() -> list[str]:
    return [
        f"每日首次唤醒 +{WAKE_XP}",
        f"每条学习活动 +{ACTIVITY_XP}（每日上限 {ACTIVITY_CAP}）",
        f"每完成一项今日必做 +{MISSION_XP}（每日上限 {MISSION_CAP}）",
        f"摸摸头 +{PAT_XP}（每日上限 {PAT_CAP}）",
        "升级所需经验逐级增加；形态在 Lv.3 / 5 / 8 / 12 解锁。",
    ]
