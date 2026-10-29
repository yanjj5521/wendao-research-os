from __future__ import annotations

from datetime import date, timedelta
from typing import Any


CAREER_PHASES = (
    {
        "key": "foundation",
        "label": "打基础",
        "icon": "基",
        "question": "我正在建立哪一种以后会反复调用的基本能力？",
    },
    {
        "key": "explore",
        "label": "广探索",
        "icon": "问",
        "question": "哪些方向值得我投入一学期去了解，哪怕最后用不上？",
    },
    {
        "key": "validate",
        "label": "练到会",
        "icon": "证",
        "question": "我怎样确认自己是真会了，而不是只是看懂了？",
    },
    {
        "key": "integrate",
        "label": "建体系",
        "icon": "合",
        "question": "零散所学能否连成一张自己的知识地图？",
    },
    {
        "key": "publish",
        "label": "讲清楚",
        "icon": "发",
        "question": "怎样讲清楚，让别人能懂、能检验、能复用？",
    },
    {
        "key": "transition",
        "label": "迁移生长",
        "icon": "迁",
        "question": "下一阶段应继承什么，又应主动放下什么？",
    },
)

CAREER_PHASE_MAP = {item["key"]: item for item in CAREER_PHASES}

MOMENT_TYPES = {
    "decision": {"label": "关键决策", "icon": "决"},
    "breakthrough": {"label": "突破时刻", "icon": "破"},
    "failure": {"label": "失败转化", "icon": "省"},
    "skill": {"label": "能力形成", "icon": "能"},
    "output": {"label": "成果落地", "icon": "果"},
    "collaboration": {"label": "合作节点", "icon": "同"},
    "transition": {"label": "方向转折", "icon": "转"},
}


def _setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def _count(conn, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["n"] or 0) if row else 0


def _streak(conn) -> int:
    """Consecutive days that left at least one real action behind."""
    rows = conn.execute(
        "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM activities ORDER BY day DESC"
    ).fetchall()
    days = {str(row["day"]) for row in rows}
    if not days:
        return 0
    cursor = date.today()
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def career_snapshot(conn) -> dict[str, Any]:
    phase_key = _setting(conn, "career_phase", "foundation")
    if phase_key not in CAREER_PHASE_MAP:
        phase_key = "foundation"
    phase = CAREER_PHASE_MAP[phase_key]

    active_project = conn.execute(
        """
        SELECT p.id,p.title,p.current_state,p.updated_at,
               m.id milestone_id,m.title milestone_title,m.criterion,m.deliverable
        FROM research_projects p
        LEFT JOIN project_milestones m
          ON m.project_id=p.id AND m.status='active'
        WHERE p.status='active'
        ORDER BY p.updated_at DESC,m.sort_order,m.id
        LIMIT 1
        """
    ).fetchone()
    daily_mission = conn.execute(
        """
        SELECT m.id,m.title,m.deliverable,m.day_index
        FROM daily_missions m
        JOIN study_plans p ON p.id=m.plan_id
        WHERE p.status='active' AND m.completed=0
        ORDER BY m.day_index,m.optional,m.sort_order,m.id
        LIMIT 1
        """
    ).fetchone()
    open_quest = conn.execute(
        """
        SELECT id,title,deliverable,difficulty,status
        FROM quests
        WHERE completed=0
        ORDER BY status='active' DESC,difficulty DESC,id
        LIMIT 1
        """
    ).fetchone()

    # Learning comes first.  A research project gate is only suggested when the
    # day's own learning actions are already accounted for; the project track is
    # a side record, not the axis the whole product steers by.
    if daily_mission:
        next_step = {
            "kind": f"近期计划 · Day {int(daily_mission['day_index'])}",
            "title": daily_mission["title"],
            "detail": daily_mission["deliverable"] or "留下最小可验证交付",
            "url": "/daily",
            "action": "进入今日学习",
        }
    elif open_quest:
        next_step = {
            "kind": "学习任务",
            "title": open_quest["title"],
            "detail": open_quest["deliverable"] or "写下这次要形成的可复用能力",
            "url": "/cultivation",
            "action": "继续推进",
        }
    elif active_project and active_project["milestone_id"]:
        next_step = {
            "kind": "课题证据闸门（附带）",
            "title": active_project["milestone_title"],
            "detail": active_project["deliverable"] or active_project["criterion"],
            "url": f"/projects/{int(active_project['id'])}",
            "action": "有空再推进",
        }
    elif active_project:
        next_step = {
            "kind": "活跃课题（附带）",
            "title": active_project["title"],
            "detail": "为课题建立下一个带证据标准的里程碑。",
            "url": f"/projects/{int(active_project['id'])}",
            "action": "补齐推进闸门",
        }
    else:
        next_step = {
            "kind": "开始学习",
            "title": "定一个三天内能完成的小计划",
            "detail": "先写清这三天想学会什么、拿什么证明自己会了，不必承诺长期主线。",
            "url": "/plans",
            "action": "建立近期计划",
        }

    counts = {
        "projects": _count(conn, "SELECT COUNT(*) n FROM research_projects"),
        "active_projects": _count(
            conn, "SELECT COUNT(*) n FROM research_projects WHERE status='active'"
        ),
        "deliveries": _count(conn, "SELECT COUNT(*) n FROM mission_deliveries"),
        "knowledge": _count(conn, "SELECT COUNT(*) n FROM entries"),
        "experiments": _count(conn, "SELECT COUNT(*) n FROM experiments"),
        "simulations": _count(conn, "SELECT COUNT(*) n FROM simulations"),
        "passed_gates": _count(
            conn, "SELECT COUNT(*) n FROM project_milestones WHERE status='passed'"
        ),
        "reviews": _count(
            conn, "SELECT COUNT(*) n FROM review_sessions WHERE status='completed'"
        ),
        "moments": _count(conn, "SELECT COUNT(*) n FROM career_moments"),
        "domains": _count(conn, "SELECT COUNT(*) n FROM workspaces WHERE active=1"),
        "open_quests": _count(conn, "SELECT COUNT(*) n FROM quests WHERE completed=0"),
        "streak": _streak(conn),
    }
    output_moments = _count(
        conn, "SELECT COUNT(*) n FROM career_moments WHERE moment_type='output'"
    )
    reflection_moments = _count(
        conn,
        "SELECT COUNT(*) n FROM career_moments WHERE moment_type IN ('decision','failure','transition')",
    )
    lifecycle = (
        {
            "key": "learn",
            "icon": "学",
            "label": "理解与训练",
            "count": counts["knowledge"] + counts["reviews"],
            "detail": "知识条目与主动复盘",
        },
        {
            "key": "question",
            "icon": "问",
            "label": "课题记录",
            "count": counts["projects"],
            "detail": "需要时把问题记清楚，不必天天推进",
        },
        {
            "key": "validate",
            "icon": "验",
            "label": "实验与计算",
            "count": counts["experiments"] + counts["simulations"] + counts["passed_gates"],
            "detail": "实验、模拟与已通过闸门",
        },
        {
            "key": "deliver",
            "icon": "成",
            "label": "交付与成果",
            "count": counts["deliveries"] + output_moments,
            "detail": "真实交付与成果节点",
        },
        {
            "key": "reflect",
            "icon": "省",
            "label": "复盘与决策",
            "count": counts["reviews"] + reflection_moments,
            "detail": "复盘、失败与方向判断",
        },
    )

    recent_moments = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.*,p.title project_title
            FROM career_moments c
            LEFT JOIN research_projects p ON p.id=c.project_id
            ORDER BY c.occurred_on DESC,c.id DESC
            LIMIT 30
            """
        )
    ]
    for moment in recent_moments:
        meta = MOMENT_TYPES.get(moment["moment_type"], MOMENT_TYPES["decision"])
        moment["type_label"] = meta["label"]
        moment["type_icon"] = meta["icon"]

    return {
        "phase": phase,
        "focus": _setting(
            conn,
            "career_focus",
            "把当前学习转化为可复用、可验证的科研能力。",
        ),
        "boundary": _setting(
            conn,
            "career_boundary",
            "不为尚未确定的长期主线提前承诺，只推进当前证据最需要的一步。",
        ),
        "success_signal": _setting(
            conn,
            "career_success_signal",
            "能独立解释、复现或用证据修正一个关键判断。",
        ),
        "review_date": _setting(conn, "career_review_date", ""),
        "counts": counts,
        "lifecycle": lifecycle,
        "next_step": next_step,
        "recent_moments": recent_moments,
        "active_project": dict(active_project) if active_project else None,
        "story": (
            f"你已经留下 {counts['deliveries']} 份真实交付、"
            f"{counts['experiments'] + counts['simulations']} 条实验或计算记录，"
            f"并保存了 {counts['moments']} 个值得带到下一阶段的生涯节点。"
        ),
    }
