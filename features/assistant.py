from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services.prompt_builder import build_plan_prompt, build_today_prompt, current_state


PROMPT_MODES = {
    "today": ("今日推进", "选一项任务，把它推进到可交付"),
    "plan": ("近期计划", "生成可直接导入的 3–7 天计划"),
    "project": ("课题审查", "检查科学问题、证据闸门和停止条件"),
    "experiment": ("实验设计", "把想法变成最小变量、对照和判据"),
    "data": ("数据分析", "从原始数据到图表、异常和结论边界"),
    "paper": ("论文研读", "按问题—图—证据链读懂论文"),
    "debug": ("排错分析", "按概率和最小改变量定位问题"),
    "mentor": ("导师沟通", "把复杂进展压缩成能讨论的短汇报"),
    "writing": ("写作整理", "把已有证据组织成摘要、段落或图注"),
}


def _int_or_none(value: str | int | None) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def register_assistant_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    @router.get("/assistant", response_class=HTMLResponse, name="assistant_page")
    def assistant_page(
        request: Request,
        mode: str = "today",
        workspace: int | None = None,
        project: int | None = None,
    ):
        selected_mode = mode if mode in PROMPT_MODES else "today"
        with connect() as conn:
            workspaces = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,name,icon,objective FROM workspaces WHERE active=1 ORDER BY sort_order,id"
                )
            ]
            workspace_ids = {int(item["id"]) for item in workspaces}
            workspace_id = workspace if workspace in workspace_ids else None
            projects = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id,title,research_question,status
                    FROM research_projects p
                    WHERE status='active'
                      AND (
                        ? IS NULL OR EXISTS (
                          SELECT 1 FROM project_workspaces pw
                          WHERE pw.project_id=p.id AND pw.workspace_id=?
                        ) OR p.workspace_id=?
                      )
                    ORDER BY p.updated_at DESC,p.id DESC
                    """,
                    (workspace_id, workspace_id, workspace_id),
                )
            ]
            project_ids = {int(item["id"]) for item in projects}
            project_id = project if project in project_ids else None
            state = current_state(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            plan = conn.execute(
                "SELECT * FROM study_plans WHERE status='active' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            recent_questions = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT title,summary,content
                    FROM entries
                    WHERE kind IN ('question','idea','failure')
                      AND (? IS NULL OR workspace_id=?)
                    ORDER BY updated_at DESC LIMIT 5
                    """,
                    (workspace_id, workspace_id),
                )
            ]
        prompt = _build_prompt(selected_mode, state, recent_questions)
        selected_workspace = next(
            (item for item in workspaces if int(item["id"]) == workspace_id),
            None,
        )
        selected_project = next(
            (item for item in projects if int(item["id"]) == project_id),
            None,
        )
        return templates.TemplateResponse(
            request=request,
            name="assistant.html",
            context=context(
                request,
                "assistant",
                mode=selected_mode,
                prompt=prompt,
                plan=dict(plan) if plan else None,
                prompt_modes=PROMPT_MODES,
                workspaces=workspaces,
                projects=projects,
                selected_workspace=selected_workspace,
                selected_project=selected_project,
                selected_workspace_id=workspace_id,
                selected_project_id=project_id,
                state_counts={
                    "tasks": len(state.get("unfinished") or []),
                    "deliveries": len(state.get("recent") or []),
                    "milestones": len(state.get("cultivation_tasks") or []),
                    "projects": len(state.get("projects") or []),
                },
            ),
        )

    @router.post("/assistant/save", name="assistant_result_save")
    def assistant_result_save(
        request: Request,
        title: str = Form("AI 协作结论"),
        summary: str = Form(...),
        evidence: str = Form(""),
        next_action: str = Form(""),
        destination: str = Form("knowledge"),
        workspace_id: str = Form(""),
        project_id: str = Form(""),
        mode: str = Form("today"),
    ):
        summary_text = summary.strip()[:8000]
        if len(summary_text) < 3:
            flash(request, "至少留下三个字的结论，AI 协作才值得保存。", "error")
            return RedirectResponse(request.url_for("assistant_page"), status_code=303)
        workspace_value = _int_or_none(workspace_id)
        project_value = _int_or_none(project_id)
        destination_value = (
            destination if destination in {"knowledge", "project", "both"} else "knowledge"
        )
        if destination_value in {"project", "both"} and project_value is None:
            flash(request, "保存到课题前，请先选择一个课题。", "error")
            return RedirectResponse(
                f"{request.url_for('assistant_page')}?mode={mode}",
                status_code=303,
            )
        ts = now_iso()
        saved: list[str] = []
        with connect() as conn:
            workspace = (
                conn.execute(
                    "SELECT name FROM workspaces WHERE id=? AND active=1",
                    (workspace_value,),
                ).fetchone()
                if workspace_value
                else None
            )
            if workspace_value and not workspace:
                workspace_value = None
            project = (
                conn.execute(
                    "SELECT id FROM research_projects WHERE id=? AND status='active'",
                    (project_value,),
                ).fetchone()
                if project_value
                else None
            )
            if project_value and not project:
                project_value = None
            if destination_value in {"project", "both"} and project_value is None:
                flash(request, "所选课题不存在或已归档，请重新选择。", "error")
                return RedirectResponse(
                    f"{request.url_for('assistant_page')}?mode={mode}",
                    status_code=303,
                )
            if destination_value in {"knowledge", "both"}:
                entry_title = title.strip()[:180] or "AI 协作结论"
                content = summary_text
                if evidence.strip():
                    content += f"\n\n证据与依据\n{evidence.strip()[:6000]}"
                if next_action.strip():
                    content += f"\n\n唯一下一行动\n{next_action.strip()[:2000]}"
                conn.execute(
                    """
                    INSERT INTO entries(
                        title,kind,domain,tags,summary,content,source,workspace_id,
                        extract_status,content_format,created_at,updated_at
                    ) VALUES (?,'note',?,'AI协作,待核验',?,?,?,?,'ready','plain',?,?)
                    """,
                    (
                        entry_title,
                        str(workspace["name"]) if workspace else "未分类",
                        summary_text[:280],
                        content,
                        f"AI 协作 · {PROMPT_MODES.get(mode, PROMPT_MODES['today'])[0]}",
                        workspace_value,
                        ts,
                        ts,
                    ),
                )
                saved.append("知识库")
            if destination_value in {"project", "both"} and project_value is not None:
                conn.execute(
                    """
                    INSERT INTO project_updates(
                        project_id,update_type,summary,evidence,next_action,confidence,created_at
                    ) VALUES (?,'decision',?,?,?,?,?)
                    """,
                    (
                        project_value,
                        summary_text,
                        evidence.strip()[:5000],
                        next_action.strip()[:2000],
                        50,
                        ts,
                    ),
                )
                conn.execute(
                    "UPDATE research_projects SET current_state=?,updated_at=? WHERE id=?",
                    (summary_text, ts, project_value),
                )
                saved.append("课题推进")
            conn.execute(
                """
                UPDATE easter_eggs
                SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                WHERE egg_key='ai_handoff'
                """,
                (ts,),
            )
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                (
                    "ai_handoff",
                    0,
                    f"AI 协作结果回存：{title.strip()[:80] or '未命名'}",
                    ts,
                ),
            )
            conn.commit()
        flash(request, f"AI 结果已保存到{'和'.join(saved)}，不会随聊天窗口消失。", "success")
        return RedirectResponse(
            f"{request.url_for('assistant_page')}?mode={mode}"
            f"{f'&workspace={workspace_value}' if workspace_value else ''}"
            f"{f'&project={project_value}' if project_value else ''}",
            status_code=303,
        )

    app.include_router(router)


def _scope_text(state: dict[str, Any]) -> str:
    workspaces = state.get("workspaces") or []
    projects = state.get("projects") or []
    workspace_text = "、".join(item["name"] for item in workspaces) or "未限定"
    project_text = "\n".join(
        f"- {item['title']}：{item.get('research_question') or '科学问题未填写'}；"
        f"下一步={item.get('next_action') or '未填写'}"
        for item in projects
    ) or "- 未限定"
    return f"工作区范围：{workspace_text}\n课题范围：\n{project_text}"


def _build_prompt(
    mode: str,
    state: dict[str, Any],
    recent_questions: list[dict[str, Any]],
) -> str:
    questions = "\n".join(f"- {q['title']}" for q in recent_questions) or "- 暂无"
    profile = state.get("profile") or {}
    goals = profile.get("goals") or "根据当前问题灵活推进"
    scope = _scope_text(state)

    if mode == "plan":
        return build_plan_prompt(state)
    if mode == "paper":
        return f"""你是材料科学与土木工程交叉领域的论文导师。请帮助我读一篇论文，但不要直接给长篇总结。

当前目标：{goals}
{scope}
最近的问题：
{questions}

先让我提供论文或摘要，然后按顺序回答：
1. 作者真正的问题是什么，为什么当时重要；
2. 每张关键图在证据链中承担什么角色；
3. 哪些结论是数据直接支持的，哪些只是解释；
4. 与我当前课题是直接基线、可借方法、反例还是相邻启发；
5. 最后只布置一个最小交付，并要求我写 3–8 条复盘关键文本。
输出最后附“可回存科研系统”区块：结论、证据、唯一下一行动。"""
    if mode == "debug":
        return f"""你是我的科研调试搭档。
{scope}

我会提供报错、实验异常或曲线。请严格按以下格式：
1. 复述我实际做了什么与已知边界；
2. 最可能的 3 个原因，按概率排序并写依据；
3. 最小排查步骤，每次只改变一个变量；
4. 需要补充的日志、图片、参数与单位；
5. 哪个结果会排除哪个原因；
6. 最后给出“结论、证据、唯一下一行动”，便于回存科研系统。
不得杜撰实验结果或把相关性写成因果。"""
    if mode == "experiment":
        return f"""你是我的实验设计审查员，优先减少无效试件和混杂变量。
当前目标：{goals}
{scope}

请先让我写出一个想验证的判断，再依次完成：
1. 改写成“变量 A 通过机制 M 影响结果 B”的可证伪假设；
2. 指出自变量、因变量、控制变量、对照和重复数；
3. 给出最小实验矩阵，不同时引入多种新材料或新测试窗口；
4. 写清统一单位、样品编号、测试顺序与通过/停止条件；
5. 预先列出三种可能结果及各自意味着什么；
6. 输出一张可执行检查表，以及“结论、证据、唯一下一行动”。
若现有信息不足，先问最多 5 个真正会改变实验设计的问题。"""
    if mode == "data":
        return f"""你是我的科研数据分析搭档。目标不是把图画漂亮，而是得到可复查的判断。
{scope}

我会提供表格、字段或分析目标。请依次：
1. 核对样品、单位、面积/质量/体积归一化口径和缺失值；
2. 区分原始值、派生值、异常值与人为剔除；
3. 给出均值、标准差、变异系数及合适图形；
4. 先描述趋势，再判断是否支持机制，不把相关性写成因果；
5. 写出最小可复现代码或表格步骤；
6. 输出“图表结论、异常、证据边界、唯一下一行动”供回存。
任何删除、插值或异常处理都必须先说明规则。"""
    if mode == "project":
        project_text = "\n".join(
            f"- {item['title']}：问题={item.get('research_question') or '未填写'}；"
            f"成功标准={item.get('success_criteria') or '未填写'}；"
            f"当前基础={item.get('current_state') or '未填写'}；"
            f"下一行动={item.get('next_action') or '未填写'}"
            for item in state.get("projects", [])
        ) or "- 尚未选择课题"
        return f"""你是我的科研课题审查员。不要替我包装故事，要帮助我决定 Go、Revise 或 Stop。
{scope}

当前课题状态：
{project_text}

请依次判断：
1. 科学问题是否清楚到可以被证伪；
2. 创新点是否只是材料替换、性能比较或换名词；
3. 当前证据最薄弱的一环与替代解释；
4. 哪个最小证据能最大幅度改变判断；
5. 未来 3–7 天只保留一个核心闸门；
6. 什么结果出现时应停止或转向。
最后输出“结论、证据、唯一下一行动”，供回存科研系统。"""
    if mode == "mentor":
        return f"""你是我的导师沟通编辑。请把复杂进展压缩成导师能迅速判断、也方便追问的短汇报。
当前目标：{goals}
{scope}

请先让我提供事实、图表或困惑，然后输出：
1. 30 秒版：我在做什么、为什么值得做、现在卡在哪里；
2. 3 分钟版：问题—方法—现有证据—风险—下一步；
3. 必须向导师确认的 1–3 个决定；
4. 绝不能夸大的结论；
5. 会前应带的图、表或原始记录。
语言简单、具体，不用空泛宏大词。最后附可回存的“讨论结论、导师意见、唯一下一行动”。"""
    if mode == "writing":
        return f"""你是我的科研写作编辑。只基于我提供的证据写作，不补造数据、文献或机制。
当前目标：{goals}
{scope}

先让我选择摘要、正文段落、图注、汇报讲稿或审稿回复，再：
1. 提取主张、证据、边界和不确定性；
2. 给出一句话中心句与段落逻辑；
3. 写出简洁版本，区分结果与解释；
4. 标出需要引文或原始数据支持的位置；
5. 提供一次反向审查：最容易被质疑什么。
最后附“可回存科研系统”的结论与待补证据。"""
    return build_today_prompt(state)
