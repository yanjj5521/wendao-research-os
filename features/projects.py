from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import connect, now_iso
from services.project_progress import (
    MILESTONE_STATUSES,
    PROJECT_STATUSES,
    build_project_prompt,
    project_state,
    render_project_plan,
    seed_project_milestones,
)
from services.scholar_search import ScholarSearchError, search_works


CASE_RELATIONS = {
    "baseline": "直接基线",
    "method": "可借方法",
    "contrast": "反例/冲突",
    "adjacent": "相邻启发",
    "unclassified": "待判断",
}

UPDATE_TYPES = {
    "checkin": "推进记录",
    "evidence": "新增证据",
    "blocker": "当前卡点",
    "decision": "关键决策",
    "failure": "失败复盘",
}


def _clean(value: str, limit: int) -> str:
    return re.sub(r"\r\n?", "\n", str(value or "")).strip()[:limit]


def _date(value: str) -> str | None:
    text = _clean(value, 10)
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _workspace_id(value: str) -> int | None:
    return int(value) if str(value or "").isdigit() else None


def _workspace_ids(values: list[str]) -> list[int]:
    return list(
        dict.fromkeys(
            int(value)
            for value in values
            if str(value or "").isdigit() and int(value) > 0
        )
    )[:24]


def _project_workspaces(conn, project_id: int) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT w.id,w.name,w.icon,w.module,w.accent,pw.role,pw.is_primary
            FROM project_workspaces pw
            JOIN workspaces w ON w.id=pw.workspace_id
            WHERE pw.project_id=?
            ORDER BY pw.is_primary DESC,w.sort_order,w.id
            """,
            (project_id,),
        )
    ]
    if rows:
        return rows
    legacy = conn.execute(
        """
        SELECT w.id,w.name,w.icon,w.module,w.accent,'' role,1 is_primary
        FROM research_projects p
        JOIN workspaces w ON w.id=p.workspace_id
        WHERE p.id=?
        """,
        (project_id,),
    ).fetchone()
    return [dict(legacy)] if legacy else []


def _set_project_workspaces(conn, project_id: int, workspace_ids: list[int]) -> None:
    valid = [
        int(row["id"])
        for row in conn.execute(
            f"SELECT id FROM workspaces WHERE id IN ({','.join('?' for _ in workspace_ids)})",
            workspace_ids,
        )
    ] if workspace_ids else []
    ordered = [item for item in workspace_ids if item in set(valid)]
    conn.execute("DELETE FROM project_workspaces WHERE project_id=?", (project_id,))
    ts = now_iso()
    for index, workspace_id in enumerate(ordered):
        conn.execute(
            """
            INSERT INTO project_workspaces(
                project_id,workspace_id,role,is_primary,created_at
            ) VALUES (?,?,?,?,?)
            """,
            (
                project_id,
                workspace_id,
                "主要工作区" if index == 0 else "协同工作区",
                int(index == 0),
                ts,
            ),
        )
    conn.execute(
        "UPDATE research_projects SET workspace_id=?,updated_at=? WHERE id=?",
        (ordered[0] if ordered else None, ts, project_id),
    )
    if len(ordered) >= 2:
        conn.execute(
            """
            UPDATE easter_eggs
            SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
            WHERE egg_key='many_workspaces'
            """,
            (ts,),
        )


def _safe_url(value: str) -> str:
    url = _clean(value, 1200)
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


CHAIN_STAGES = (
    ("literature", "文献", "先收一篇与问题直接相关的文献"),
    ("thinking", "思考", "写一条问题、判断或失败复盘"),
    ("experiment", "实验", "记一个带边界的实验批次"),
    ("data", "数据", "建一个可追溯的数据集档案"),
    ("analysis", "分析", "归档一次模拟、代码或推导"),
    ("result", "成果", "通过一道闸门，或记一个成果节点"),
)


def evidence_chain(conn, project_id: int, linked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count the six links of the research loop for one project.

    A project page that only shows gates tells you whether you may pass; it does
    not tell you which link is missing.  This is the 1.0 spine: every stage is
    countable, clickable and allowed to be empty.
    """
    ids = [int(item["id"]) for item in linked]
    counts = {"literature": 0, "thinking": 0, "experiment": 0, "data": 0, "analysis": 0, "result": 0}
    scope = ""
    if ids:
        marks = ",".join("?" * len(ids))
        scope = f"&workspace={ids[0]}" if len(ids) == 1 else ""
        row = conn.execute(
            f"""
            SELECT
              SUM(CASE WHEN kind='document' THEN 1 ELSE 0 END) AS literature,
              SUM(CASE WHEN kind IN ('note','question','idea','failure','sop') THEN 1 ELSE 0 END) AS thinking,
              SUM(CASE WHEN kind='dataset' THEN 1 ELSE 0 END) AS data,
              SUM(CASE WHEN kind='code' THEN 1 ELSE 0 END) AS analysis
            FROM entries WHERE status='active' AND workspace_id IN ({marks})
            """,
            ids,
        ).fetchone()
        counts["literature"] = int(row["literature"] or 0)
        counts["thinking"] = int(row["thinking"] or 0)
        counts["data"] = int(row["data"] or 0)
        counts["analysis"] = int(row["analysis"] or 0)
        counts["experiment"] = int(
            conn.execute(
                f"SELECT COUNT(*) n FROM experiments WHERE workspace_id IN ({marks})", ids
            ).fetchone()["n"]
        )
        counts["analysis"] += int(
            conn.execute(
                f"SELECT COUNT(*) n FROM simulations WHERE workspace_id IN ({marks})", ids
            ).fetchone()["n"]
        )
    counts["result"] = int(
        conn.execute(
            """
            SELECT COUNT(*) n FROM career_moments
            WHERE project_id=? AND moment_type IN ('output','breakthrough','skill')
            """,
            (project_id,),
        ).fetchone()["n"]
    )
    urls = {
        "literature": f"/library?kind=document{scope}",
        "thinking": f"/library{('?' + scope[1:]) if scope else ''}",
        "experiment": f"/experiments{('?' + scope[1:]) if scope else ''}",
        "data": f"/library?kind=dataset{scope}",
        "analysis": f"/simulations{('?' + scope[1:]) if scope else ''}",
        "result": f"/projects/{project_id}#gates",
    }
    return [
        {
            "key": key,
            "label": label,
            "hint": hint,
            "count": counts[key],
            "url": urls[key],
        }
        for key, label, hint in CHAIN_STAGES
    ]


def _project_or_404(conn, project_id: int):
    row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404)
    return row


def _go(request: Request, name: str, **params: Any) -> RedirectResponse:
    return RedirectResponse(request.url_for(name, **params), status_code=303)


def register_project_routes(
    app,
    templates,
    context: Callable[..., dict[str, Any]],
    flash: Callable[[Request, str, str], None],
):
    router = APIRouter()

    @router.get("/projects", response_class=HTMLResponse, name="projects_page")
    def projects_page(request: Request, status: str = "active"):
        selected_status = status if status in {*PROJECT_STATUSES, "all"} else "active"
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*
                FROM research_projects p
                WHERE (?='all' OR p.status=?)
                ORDER BY p.status='active' DESC,p.updated_at DESC,p.id DESC
                """,
                (selected_status, selected_status),
            ).fetchall()
            projects: list[dict[str, Any]] = []
            for row in rows:
                project = dict(row)
                linked_workspaces = _project_workspaces(conn, int(row["id"]))
                project["workspaces"] = linked_workspaces
                project["workspace_name"] = "、".join(
                    item["name"] for item in linked_workspaces
                )
                milestones = [
                    dict(item)
                    for item in conn.execute(
                        "SELECT * FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                        (row["id"],),
                    )
                ]
                cases = [
                    dict(item)
                    for item in conn.execute(
                        "SELECT * FROM project_cases WHERE project_id=? ORDER BY created_at DESC",
                        (row["id"],),
                    )
                ]
                updates = [
                    dict(item)
                    for item in conn.execute(
                        "SELECT * FROM project_updates WHERE project_id=? ORDER BY id DESC LIMIT 1",
                        (row["id"],),
                    )
                ]
                project["state"] = project_state(project, milestones, cases, updates)
                project["case_count"] = len(cases)
                project["update_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) n FROM project_updates WHERE project_id=?",
                        (row["id"],),
                    ).fetchone()["n"]
                )
                projects.append(project)
            workspaces = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,name,icon FROM workspaces WHERE active=1 ORDER BY sort_order,id"
                )
            ]
        return templates.TemplateResponse(
            request=request,
            name="projects.html",
            context=context(
                request,
                "projects",
                projects=projects,
                selected_status=selected_status,
                project_statuses=PROJECT_STATUSES,
                workspaces=workspaces,
            ),
        )

    @router.post("/projects/new", name="project_new")
    def project_new(
        request: Request,
        title: str = Form(...),
        research_question: str = Form(""),
        rationale: str = Form(""),
        target_outcome: str = Form(""),
        success_criteria: str = Form(""),
        current_state: str = Form(""),
        constraints_text: str = Form(""),
        search_query: str = Form(""),
        workspace_ids: list[str] = Form(default=[]),
        target_date: str = Form(""),
    ):
        project_title = _clean(title, 160)
        if not project_title:
            flash(request, "课题名称不能为空。", "error")
            return _go(request, "projects_page")
        values = {
            "research_question": _clean(research_question, 3000),
            "rationale": _clean(rationale, 3000),
            "target_outcome": _clean(target_outcome, 3000),
            "success_criteria": _clean(success_criteria, 5000),
            "current_state": _clean(current_state, 5000),
            "constraints_text": _clean(constraints_text, 3000),
            "search_query": _clean(search_query, 500),
        }
        ts = now_iso()
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO research_projects(
                    title,research_question,rationale,target_outcome,success_criteria,current_state,
                    constraints_text,search_query,workspace_id,status,target_date,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?)
                """,
                (
                    project_title,
                    values["research_question"],
                    values["rationale"],
                    values["target_outcome"],
                    values["success_criteria"],
                    values["current_state"],
                    values["constraints_text"],
                    values["search_query"],
                    None,
                    _date(target_date),
                    ts,
                    ts,
                ),
            )
            project_id = int(cur.lastrowid)
            _set_project_workspaces(
                conn,
                project_id,
                _workspace_ids(workspace_ids),
            )
            seed_project_milestones(conn, project_id, values["success_criteria"])
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("project_created", 0, f"建立课题推进：{project_title}", ts),
            )
            conn.commit()
        flash(request, "课题已建立，并生成 5 个可修改的证据闸门。", "success")
        suffix = f"?q={values['search_query']}&search=1" if values["search_query"] else ""
        return RedirectResponse(
            f"{request.url_for('project_page', project_id=project_id)}{suffix}",
            status_code=303,
        )

    @router.get("/projects/{project_id}", response_class=HTMLResponse, name="project_page")
    def project_page(request: Request, project_id: int, q: str = "", search: int = 0):
        query = _clean(q, 500)
        results: list[dict[str, Any]] = []
        search_error = ""
        with connect() as conn:
            project_row = _project_or_404(conn, project_id)
            project = dict(project_row)
            milestones = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                    (project_id,),
                )
            ]
            cases = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_cases WHERE project_id=? ORDER BY created_at DESC,id DESC",
                    (project_id,),
                )
            ]
            updates = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_updates WHERE project_id=? ORDER BY id DESC LIMIT 30",
                    (project_id,),
                )
            ]
            workspaces = [
                dict(row)
                for row in conn.execute(
                    "SELECT id,name,icon FROM workspaces WHERE active=1 ORDER BY sort_order,id"
                )
            ]
            linked_workspaces = _project_workspaces(conn, project_id)
            workspace = linked_workspaces[0] if linked_workspaces else None
            project["workspace_names"] = "、".join(
                item["name"] for item in linked_workspaces
            )
            chain = evidence_chain(conn, project_id, linked_workspaces)
        if search and query:
            try:
                results = search_works(query, limit=10, timeout=10)
            except (ScholarSearchError, ValueError) as exc:
                search_error = str(exc)
        state = project_state(project, milestones, cases, updates)
        for item in milestones:
            item["status_label"] = MILESTONE_STATUSES.get(item["status"], item["status"])
        prompt = build_project_prompt(project, state, cases, updates)
        return templates.TemplateResponse(
            request=request,
            name="project.html",
            context=context(
                request,
                "projects",
                project=project,
                state=state,
                chain=chain,
                milestones=milestones,
                cases=cases,
                updates=updates,
                workspaces=workspaces,
                workspace=dict(workspace) if workspace else None,
                project_workspaces=linked_workspaces,
                project_workspace_ids={
                    int(item["id"]) for item in linked_workspaces
                },
                project_statuses=PROJECT_STATUSES,
                milestone_statuses=MILESTONE_STATUSES,
                case_relations=CASE_RELATIONS,
                update_types=UPDATE_TYPES,
                q=query,
                search_results=results,
                search_error=search_error,
                project_prompt=prompt,
            ),
        )

    @router.post("/projects/{project_id}/save", name="project_save")
    def project_save(
        request: Request,
        project_id: int,
        title: str = Form(...),
        research_question: str = Form(""),
        rationale: str = Form(""),
        target_outcome: str = Form(""),
        success_criteria: str = Form(""),
        current_state: str = Form(""),
        constraints_text: str = Form(""),
        search_query: str = Form(""),
        workspace_ids: list[str] = Form(default=[]),
        target_date: str = Form(""),
    ):
        project_title = _clean(title, 160)
        if not project_title:
            flash(request, "课题名称不能为空。", "error")
            return _go(request, "project_page", project_id=project_id)
        criterion = _clean(success_criteria, 5000)
        ts = now_iso()
        with connect() as conn:
            _project_or_404(conn, project_id)
            conn.execute(
                """
                UPDATE research_projects
                SET title=?,research_question=?,rationale=?,target_outcome=?,success_criteria=?,
                    current_state=?,constraints_text=?,search_query=?,workspace_id=?,target_date=?,updated_at=?
                WHERE id=?
                """,
                (
                    project_title,
                    _clean(research_question, 3000),
                    _clean(rationale, 3000),
                    _clean(target_outcome, 3000),
                    criterion,
                    _clean(current_state, 5000),
                    _clean(constraints_text, 3000),
                    _clean(search_query, 500),
                    None,
                    _date(target_date),
                    ts,
                    project_id,
                ),
            )
            _set_project_workspaces(
                conn,
                project_id,
                _workspace_ids(workspace_ids),
            )
            if criterion:
                conn.execute(
                    """
                    UPDATE project_milestones
                    SET criterion=?,updated_at=?
                    WHERE project_id=? AND stage_key='outcome' AND status!='passed'
                    """,
                    (f"逐条核对：{criterion[:1200]}", ts, project_id),
                )
            conn.commit()
        flash(request, "课题定义已更新。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/status", name="project_status")
    def project_status(
        request: Request,
        project_id: int,
        status: str = Form("active"),
    ):
        value = status if status in PROJECT_STATUSES else "active"
        with connect() as conn:
            _project_or_404(conn, project_id)
            conn.execute(
                "UPDATE research_projects SET status=?,updated_at=? WHERE id=?",
                (value, now_iso(), project_id),
            )
            conn.commit()
        flash(request, f"课题状态已设为“{PROJECT_STATUSES[value]}”。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/cases/save", name="project_case_save")
    def project_case_save(
        request: Request,
        project_id: int,
        provider: str = Form("Crossref"),
        external_id: str = Form(...),
        title: str = Form(...),
        authors: str = Form(""),
        publication_year: str = Form(""),
        source: str = Form(""),
        doi: str = Form(""),
        url: str = Form(""),
        cited_by: int = Form(0),
        relation: str = Form("unclassified"),
        note: str = Form(""),
    ):
        case_title = _clean(title, 1000)
        identity = _clean(external_id, 1200)
        if not case_title or not identity:
            flash(request, "这条先例缺少标题或唯一标识，未保存。", "error")
            return _go(request, "project_page", project_id=project_id)
        year_value = int(publication_year) if publication_year.isdigit() else None
        relation_value = relation if relation in CASE_RELATIONS else "unclassified"
        ts = now_iso()
        with connect() as conn:
            _project_or_404(conn, project_id)
            conn.execute(
                """
                INSERT INTO project_cases(
                    project_id,provider,external_id,title,authors,publication_year,source,doi,url,
                    cited_by,relation,note,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,provider,external_id) DO UPDATE SET
                    relation=excluded.relation,
                    note=CASE WHEN trim(excluded.note)!='' THEN excluded.note ELSE project_cases.note END
                """,
                (
                    project_id,
                    _clean(provider, 60) or "Crossref",
                    identity,
                    case_title,
                    _clean(authors, 1200),
                    year_value,
                    _clean(source, 600),
                    _clean(doi, 300),
                    _safe_url(url),
                    max(0, int(cited_by or 0)),
                    relation_value,
                    _clean(note, 2000),
                    ts,
                ),
            )
            conn.execute(
                "UPDATE research_projects SET updated_at=? WHERE id=?",
                (ts, project_id),
            )
            conn.commit()
        flash(request, "相关先例已保存到本课题。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/cases/{case_id}/save", name="project_case_edit")
    def project_case_edit(
        request: Request,
        project_id: int,
        case_id: int,
        relation: str = Form("unclassified"),
        note: str = Form(""),
    ):
        value = relation if relation in CASE_RELATIONS else "unclassified"
        with connect() as conn:
            _project_or_404(conn, project_id)
            changed = conn.execute(
                "UPDATE project_cases SET relation=?,note=? WHERE id=? AND project_id=?",
                (value, _clean(note, 2000), case_id, project_id),
            ).rowcount
            if not changed:
                raise HTTPException(status_code=404)
            conn.execute(
                "UPDATE research_projects SET updated_at=? WHERE id=?",
                (now_iso(), project_id),
            )
            conn.commit()
        flash(request, "先例关系已更新。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/milestones/{milestone_id}/save", name="project_milestone_save")
    def project_milestone_save(
        request: Request,
        project_id: int,
        milestone_id: int,
        title: str = Form(...),
        criterion: str = Form(""),
        deliverable: str = Form(""),
        status: str = Form("planned"),
        due_date: str = Form(""),
        evidence: str = Form(""),
        decision: str = Form(""),
    ):
        value = status if status in MILESTONE_STATUSES else "planned"
        evidence_text = _clean(evidence, 5000)
        decision_text = _clean(decision, 3000)
        if value == "passed" and not (evidence_text or decision_text):
            flash(request, "证据闸门不能空手通过：请写证据或决策理由。", "error")
            return _go(request, "project_page", project_id=project_id)
        ts = now_iso()
        with connect() as conn:
            milestone = conn.execute(
                "SELECT * FROM project_milestones WHERE id=? AND project_id=?",
                (milestone_id, project_id),
            ).fetchone()
            if not milestone:
                raise HTTPException(status_code=404)
            if value == "active":
                conn.execute(
                    """
                    UPDATE project_milestones
                    SET status='planned',updated_at=?
                    WHERE project_id=? AND status='active' AND id!=?
                    """,
                    (ts, project_id, milestone_id),
                )
            conn.execute(
                """
                UPDATE project_milestones
                SET title=?,criterion=?,deliverable=?,status=?,due_date=?,evidence=?,decision=?,updated_at=?
                WHERE id=? AND project_id=?
                """,
                (
                    _clean(title, 300),
                    _clean(criterion, 3000),
                    _clean(deliverable, 1000),
                    value,
                    _date(due_date),
                    evidence_text,
                    decision_text,
                    ts,
                    milestone_id,
                    project_id,
                ),
            )
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) n FROM project_milestones WHERE project_id=? AND status='active'",
                    (project_id,),
                ).fetchone()["n"]
            )
            if value == "passed" and active_count == 0:
                next_row = conn.execute(
                    """
                    SELECT id FROM project_milestones
                    WHERE project_id=? AND status IN ('planned','revise') AND sort_order>?
                    ORDER BY sort_order,id LIMIT 1
                    """,
                    (project_id, milestone["sort_order"]),
                ).fetchone()
                if next_row:
                    conn.execute(
                        "UPDATE project_milestones SET status='active',updated_at=? WHERE id=?",
                        (ts, next_row["id"]),
                    )
            conn.execute(
                "UPDATE research_projects SET updated_at=? WHERE id=?",
                (ts, project_id),
            )
            if value == "passed":
                conn.execute(
                    """
                    UPDATE easter_eggs
                    SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                    WHERE egg_key='evidence_gate'
                    """,
                    (ts,),
                )
            conn.commit()
        flash(request, "证据闸门已更新。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/milestones/new", name="project_milestone_new")
    def project_milestone_new(
        request: Request,
        project_id: int,
        title: str = Form(...),
        criterion: str = Form(""),
        deliverable: str = Form(""),
    ):
        milestone_title = _clean(title, 300)
        if not milestone_title:
            flash(request, "闸门名称不能为空。", "error")
            return _go(request, "project_page", project_id=project_id)
        ts = now_iso()
        with connect() as conn:
            _project_or_404(conn, project_id)
            order = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0)+10 n FROM project_milestones WHERE project_id=?",
                    (project_id,),
                ).fetchone()["n"]
            )
            conn.execute(
                """
                INSERT INTO project_milestones(
                    project_id,stage_key,title,criterion,deliverable,status,sort_order,created_at,updated_at
                ) VALUES (?,'custom',?,?,?,'planned',?,?,?)
                """,
                (
                    project_id,
                    milestone_title,
                    _clean(criterion, 3000),
                    _clean(deliverable, 1000),
                    order,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE research_projects SET updated_at=? WHERE id=?",
                (ts, project_id),
            )
            conn.commit()
        flash(request, "自定义证据闸门已添加。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.post("/projects/{project_id}/updates/new", name="project_update_new")
    def project_update_new(
        request: Request,
        project_id: int,
        update_type: str = Form("checkin"),
        summary: str = Form(...),
        evidence: str = Form(""),
        next_action: str = Form(""),
        confidence: int = Form(50),
    ):
        summary_text = _clean(summary, 5000)
        if not summary_text:
            flash(request, "推进记录不能为空。", "error")
            return _go(request, "project_page", project_id=project_id)
        value = update_type if update_type in UPDATE_TYPES else "checkin"
        ts = now_iso()
        with connect() as conn:
            _project_or_404(conn, project_id)
            conn.execute(
                """
                INSERT INTO project_updates(
                    project_id,update_type,summary,evidence,next_action,confidence,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    project_id,
                    value,
                    summary_text,
                    _clean(evidence, 5000),
                    _clean(next_action, 2000),
                    max(0, min(int(confidence or 50), 100)),
                    ts,
                ),
            )
            conn.execute(
                "UPDATE research_projects SET current_state=?,updated_at=? WHERE id=?",
                (summary_text, ts, project_id),
            )
            if value == "failure":
                conn.execute(
                    """
                    UPDATE easter_eggs
                    SET unlocked=1,discovered_at=COALESCE(discovered_at,?)
                    WHERE egg_key='failure_alchemy'
                    """,
                    (ts,),
                )
            conn.commit()
        flash(request, "推进记录已保存，当前基础也已同步更新。", "success")
        return _go(request, "project_page", project_id=project_id)

    @router.get("/projects/{project_id}/plan", response_class=HTMLResponse, name="project_plan")
    def project_plan(request: Request, project_id: int):
        with connect() as conn:
            project = dict(_project_or_404(conn, project_id))
            milestones = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                    (project_id,),
                )
            ]
            cases = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_cases WHERE project_id=? ORDER BY created_at DESC",
                    (project_id,),
                )
            ]
            updates = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_updates WHERE project_id=? ORDER BY id DESC LIMIT 20",
                    (project_id,),
                )
            ]
            linked_workspaces = _project_workspaces(conn, project_id)
        state = project_state(project, milestones, cases, updates)
        plan_text = render_project_plan(
            project,
            state.get("current"),
            cases,
            "、".join(item["name"] for item in linked_workspaces),
        )
        return templates.TemplateResponse(
            request=request,
            name="project_plan.html",
            context=context(
                request,
                "projects",
                project=project,
                plan_text=plan_text,
                state=state,
            ),
        )

    app.include_router(router)
