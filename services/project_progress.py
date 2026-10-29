from __future__ import annotations

import re
from typing import Any

from core.database import now_iso


DEFAULT_MILESTONES = (
    {
        "stage_key": "scope",
        "title": "界定可检验问题",
        "criterion": "用一句可证伪的问题说清研究对象、关键变量、边界条件和必要对照。",
        "deliverable": "一页问题定义卡",
    },
    {
        "stage_key": "precedent",
        "title": "建立先例与差距地图",
        "criterion": "保存至少 3 篇关键先例，并分别说明它们提供的基线、方法、相邻思路或反例。",
        "deliverable": "先例—差距对照表",
    },
    {
        "stage_key": "feasibility",
        "title": "通过最小可行验证",
        "criterion": "完成一个可重复的最小验证，保留方法、原始结果、失败条件和必要对照。",
        "deliverable": "基线验证记录",
    },
    {
        "stage_key": "evidence",
        "title": "形成主张—证据链",
        "criterion": "关键主张至少由两类相互独立的证据支撑，并记录替代解释与反例。",
        "deliverable": "主张—证据—反例矩阵",
    },
    {
        "stage_key": "outcome",
        "title": "核对成功标准并决策",
        "criterion": "逐条核对预先写下的成功标准，明确继续、修正、转向或停止。",
        "deliverable": "结题判断与下一决策",
    },
)


MILESTONE_STATUSES = {
    "planned": "待推进",
    "active": "当前闸门",
    "passed": "已通过",
    "revise": "需修正",
    "stopped": "已停止",
}

PROJECT_STATUSES = {
    "active": "推进中",
    "paused": "已暂停",
    "completed": "已完成",
    "stopped": "已停止",
}


def seed_project_milestones(conn, project_id: int, success_criteria: str = "") -> None:
    ts = now_iso()
    for order, item in enumerate(DEFAULT_MILESTONES):
        criterion = item["criterion"]
        if item["stage_key"] == "outcome" and success_criteria.strip():
            criterion = f"逐条核对：{success_criteria.strip()[:1200]}"
        conn.execute(
            """
            INSERT INTO project_milestones(
                project_id,stage_key,title,criterion,deliverable,status,sort_order,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                project_id,
                item["stage_key"],
                item["title"],
                criterion,
                item["deliverable"],
                "active" if order == 0 else "planned",
                order * 10,
                ts,
                ts,
            ),
        )


def _contains_objective_signal(value: str) -> bool:
    text = value or ""
    return bool(
        re.search(r"\d", text)
        or any(
            marker in text
            for marker in (
                "至少",
                "不低于",
                "不高于",
                "达到",
                "通过",
                "可重复",
                "显著",
                "误差",
                "置信",
                "完成",
                "一致",
            )
        )
    )


def project_state(
    project: dict[str, Any],
    milestones: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(milestones)
    passed = sum(1 for item in milestones if item.get("status") == "passed")
    current = next((item for item in milestones if item.get("status") == "active"), None)
    if current is None:
        current = next(
            (item for item in milestones if item.get("status") in {"planned", "revise"}),
            milestones[-1] if milestones else None,
        )

    checks = [
        ("科学问题", bool(str(project.get("research_question") or "").strip())),
        ("目标成果", bool(str(project.get("target_outcome") or "").strip())),
        ("成功标准", bool(str(project.get("success_criteria") or "").strip())),
        ("当前基础", bool(str(project.get("current_state") or "").strip())),
        ("检索词", bool(str(project.get("search_query") or "").strip())),
    ]
    readiness = round(sum(1 for _, ok in checks if ok) / len(checks) * 100)
    warnings: list[str] = []
    if not _contains_objective_signal(str(project.get("success_criteria") or "")):
        warnings.append("成功标准还不够可判定，建议加入数值、单位、重复次数或明确的通过条件。")
    if len(cases) < 3:
        warnings.append("关键先例不足 3 篇，暂时不能可靠判断“别人没做过”或“这条路线可行”。")
    if not updates:
        warnings.append("还没有推进记录；先写一条当前证据、卡点和下一步。")
    elif updates[0].get("update_type") == "blocker":
        warnings.append("最新记录仍是卡点，需要先给它一个验证或绕行方案。")

    if not str(project.get("search_query") or "").strip():
        next_action = "补一个英文检索式，让系统能够查找相关论文。"
    elif len(cases) < 3:
        next_action = "联网检索并保存至少 3 篇关键先例，再标注它们与课题的关系。"
    elif current:
        next_action = str(current.get("deliverable") or current.get("criterion") or current.get("title"))
    else:
        next_action = "逐条核对成功标准，明确继续、修正、转向或停止。"

    return {
        "passed": passed,
        "total": total,
        "progress": round(passed / total * 100) if total else 0,
        "current": current,
        "readiness": readiness,
        "checks": checks,
        "warnings": warnings,
        "next_action": next_action,
        "status_label": PROJECT_STATUSES.get(str(project.get("status")), "推进中"),
    }


def _single_line(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "｜")
    return text[:limit]


def render_project_plan(
    project: dict[str, Any],
    milestone: dict[str, Any] | None,
    cases: list[dict[str, Any]],
    workspace_name: str = "",
) -> str:
    title = _single_line(project.get("title"), 80) or "未命名课题"
    gate = _single_line((milestone or {}).get("title"), 100) or "核对成功标准"
    criterion = _single_line((milestone or {}).get("criterion"), 260) or _single_line(
        project.get("success_criteria"), 260
    )
    deliverable = _single_line((milestone or {}).get("deliverable"), 160) or "一份可核验记录"
    relation = f" | 工作区：{_single_line(workspace_name, 120)}" if workspace_name else ""
    project_relation = f" | 课题：{title}"
    if len(cases) < 3:
        literature_action = "检索并筛选 6–10 篇相关论文，保存至少 3 篇关键先例"
        literature_delivery = "先例清单（每篇写一句与本课题的关系）"
    else:
        literature_action = f"比较已保存的 {len(cases)} 篇先例，找出共同基线、差异变量和未解决问题"
        literature_delivery = "先例—差距对照表"
    return f"""# {title} · 三日推进
> 只突破当前证据闸门：{gate}
## 修炼任务
- [进阶] 通过“{gate}”证据闸门 | 验收：{criterion or deliverable}{relation}
## Day 1 | 先例与判据
- [重点] {literature_action} | 60min | 交付：{literature_delivery}{project_relation}
- [工具] 把当前闸门改写成“通过/不通过”判定表 | 35min | 交付：判定表（含边界、对照与失败条件）{project_relation}
## Day 2 | 最小验证
- [重点] 围绕“{gate}”执行一个最小可验证行动 | 75min | 交付：{deliverable}{project_relation}
- [工具] 保存原始记录、参数、版本与异常 | 25min | 交付：可复现记录{project_relation}
## Day 3 | 证据决策
- [重点] 将结果写成“主张—证据—反例—边界”四列表 | 55min | 交付：证据矩阵{project_relation}
- [重点] 按成功标准做 Go / Revise / Stop 决策，并只保留一个下一行动 | 30min | 交付：一段决策记录{project_relation}
"""


def build_project_prompt(
    project: dict[str, Any],
    state: dict[str, Any],
    cases: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> str:
    case_text = "\n".join(
        f"- {item.get('title')}（{item.get('publication_year') or '年份未知'}，"
        f"{item.get('relation') or '未分类'}，DOI/来源：{item.get('doi') or item.get('url') or '未记录'}）"
        for item in cases[:12]
    ) or "- 尚未保存相关先例"
    update_text = "\n".join(
        f"- [{item.get('update_type')}] {item.get('summary')}；证据：{item.get('evidence') or '未写'}；"
        f"下一步：{item.get('next_action') or '未写'}"
        for item in updates[:8]
    ) or "- 尚无推进记录"
    current = state.get("current") or {}
    return f"""你是我的科研课题推进审查员。请把课题当成一个可被证据否定或修正的项目，而不是替我包装故事。

课题：{project.get('title') or '未命名'}
科学问题：{project.get('research_question') or '未填写'}
为什么值得做：{project.get('rationale') or '未填写'}
目标成果：{project.get('target_outcome') or '未填写'}
成功标准：{project.get('success_criteria') or '未填写'}
当前基础：{project.get('current_state') or '未填写'}
现实约束：{project.get('constraints_text') or '未填写'}
关联工作区：{project.get('workspace_names') or '未关联'}
当前证据闸门：{current.get('title') or '未设置'}
闸门通过条件：{current.get('criterion') or '未设置'}

已保存先例：
{case_text}

最近推进记录：
{update_text}

请先联网核对最新文献与相近案例，再按以下顺序回答：
1. 用最简单的一段话重述“我到底要验证什么”；
2. 指出课题中最可能混淆的因果、替代解释或伪创新；
3. 列出 3–6 个最关键先例，并说明它们与本课题的真实关系，不得虚构 DOI；
4. 判断当前闸门应为 Go、Revise 还是 Stop，并给出证据理由；
5. 只给未来 3–7 天可执行的下一轮计划，每项必须有真实交付；
6. 明确什么结果出现时应停止当前路线，而不是继续堆材料。
"""
