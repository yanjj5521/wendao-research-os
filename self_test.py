from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import date, timedelta

from fastapi.testclient import TestClient
from PIL import Image

# The test command must never inherit a researcher's development database merely
# because it was launched from beside the source tree. Callers can still provide
# an explicit disposable directory when they need to inspect the generated data.
if not os.environ.get("RESEARCH_OS_DATA_DIR", "").strip():
    os.environ["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(
        prefix="research-os-self-test-"
    )

import web.app as app
from web.runtime import PROFILE_DIR, navigation_layout
from core.database import connect, get_setting, now_iso, set_setting, total_xp
from core.navigation import normalize_nav_layout
from core.runtime_paths import USER_CONFIG_DIR
from services.progression import (
    REALM_STAGES,
    TRIBULATION_GATES,
    realm_state,
)
from services.plan_import import parse_plan_text
from services.prompt_builder import current_state
from services.scholar_search import parse_crossref_payload
from core.version import APP_VERSION


def _check_response(failures: list[str], label: str, response, expected: int = 200) -> None:
    if response.status_code != expected:
        failures.append(f"{label}: HTTP {response.status_code}")


def _check_plan_copy_parser(failures: list[str]) -> None:
    rendered_copy = """**电化学资产化入门计划**
用5天把电化学学习转化为可复用的概念表、计算模板、判读清单和案例记录

修炼任务
• [进阶] 建立电化学最小概念与计算资产 | 验收：概念表与计算模板

第一天｜电化学量纲
• [必做] 串起 Q、I、V、C、E 与 P | 45分钟 | 交付物：一张概念图 | 关联修炼：建立电化学最小概念与计算资产

第三天：CV 判读
[可选] 对比两张 CV 曲线 | 20min | 交付：三句话判读
"""
    try:
        parsed = parse_plan_text(rendered_copy)
    except ValueError as exc:
        failures.append(f"rendered plan copy was rejected: {exc}")
        return
    if parsed.name != "电化学资产化入门计划":
        failures.append("rendered plan copy lost its plain-text title")
    if [day.index for day in parsed.days] != [1, 2]:
        failures.append("copied non-contiguous day labels were not normalized")
    if len(parsed.days) != 2 or not parsed.days[0].missions:
        failures.append("rendered plan copy did not create daily missions")
    elif parsed.days[0].missions[0].deliverable != "一张概念图":
        failures.append("rendered plan copy lost its deliverable field")


def _check_scholar_parser(failures: list[str]) -> None:
    payload = {
        "message": {
            "items": [
                {
                    "title": ["Evidence-gated research"],
                    "DOI": "10.1234/self-test",
                    "author": [{"given": "Lin", "family": "Qiu"}],
                    "published": {"date-parts": [[2025, 7, 1]]},
                    "container-title": ["Research Methods"],
                    "is-referenced-by-count": "not-a-number",
                    "URL": "https://doi.org/10.1234/self-test",
                },
                {
                    "title": ["Unsafe source link"],
                    "URL": "javascript:alert(1)",
                }
            ]
        }
    }
    works = parse_crossref_payload(payload)
    if len(works) != 2:
        failures.append("Crossref parser did not return normalized works")
        return
    item = works[0]
    if (
        item.get("title") != "Evidence-gated research"
        or item.get("doi") != "10.1234/self-test"
        or item.get("year") != 2025
        or item.get("authors") != "Lin Qiu"
        or item.get("cited_by") != 0
    ):
        failures.append("Crossref parser lost core scholarly metadata")
    if works[1].get("url"):
        failures.append("Crossref parser kept an unsafe source URL")


def _run_integration(client: TestClient, failures: list[str]) -> None:
    """Exercise the complete current release loop.

    This mode writes test records, so run it only against a disposable copy:
    `python self_test.py --integration`.
    """

    career_page = client.get("/career")
    _check_response(failures, "career compass", career_page)
    for marker in ("成长罗盘", "现在最值得推进的一步", "长期成长不是文件堆积", "值得带到下一阶段"):
        if marker not in career_page.text:
            failures.append(f"career compass missed: {marker}")
    _check_response(
        failures,
        "career focus save",
        client.post(
            "/career/focus",
            data={
                "phase": "validate",
                "focus": "建立可以区分电子通路与离子可达性的证据链。",
                "boundary": "不把单批次相关性写成机制结论。",
                "success_signal": "三批重复与两类独立证据给出同一判断。",
                "review_date": (date.today() + timedelta(days=30)).isoformat(),
            },
        ),
    )
    _check_response(
        failures,
        "career moment create",
        client.post(
            "/career/moments/new",
            data={
                "moment_type": "failure",
                "title": "把一次异常结果转为边界条件",
                "summary": "压实过高后电容没有继续增加。",
                "evidence": "原始曲线、样品照片和复测记录均已保存。",
                "project_id": "0",
                "occurred_on": date.today().isoformat(),
            },
        ),
    )
    with connect() as conn:
        career_phase = conn.execute(
            "SELECT value FROM settings WHERE key='career_phase'"
        ).fetchone()
        career_moment = conn.execute(
            "SELECT * FROM career_moments WHERE title='把一次异常结果转为边界条件'"
        ).fetchone()
    if not career_phase or career_phase["value"] != "validate":
        failures.append("career compass did not persist its current phase")
    if not career_moment or career_moment["moment_type"] != "failure":
        failures.append("career timeline did not persist a failure-to-learning moment")

    world_before = client.get("/world")
    _check_response(failures, "starter artifact storefront", world_before)
    if "购入 · 12 灵石" not in world_before.text:
        failures.append("starter gift could not visibly afford the starter artifact")
    _check_response(
        failures,
        "starter artifact exact-balance purchase",
        client.post("/world/artifacts/qingxin_slip/buy"),
    )
    with connect() as conn:
        starter_artifact = conn.execute(
            "SELECT * FROM inventory_items WHERE item_key='qingxin_slip'"
        ).fetchone()
        stone_after_purchase = conn.execute(
            "SELECT COALESCE(SUM(amount),0) n FROM asset_transactions WHERE asset_key='spirit_stone'"
        ).fetchone()["n"]
    if not starter_artifact or int(stone_after_purchase) != 0:
        failures.append("exact-balance artifact purchase was not atomic")
    _check_response(
        failures,
        "duplicate artifact purchase protection",
        client.post("/world/artifacts/qingxin_slip/buy"),
    )
    _check_response(
        failures,
        "insufficient artifact purchase protection",
        client.post("/world/artifacts/measuring_ruler/buy"),
    )
    with connect() as conn:
        duplicate_count = conn.execute(
            "SELECT COUNT(*) n FROM inventory_items WHERE item_key='qingxin_slip'"
        ).fetchone()["n"]
        unaffordable = conn.execute(
            "SELECT 1 FROM inventory_items WHERE item_key='measuring_ruler'"
        ).fetchone()
        stone_after_rejections = conn.execute(
            "SELECT COALESCE(SUM(amount),0) n FROM asset_transactions WHERE asset_key='spirit_stone'"
        ).fetchone()["n"]
    if duplicate_count != 1 or unaffordable or int(stone_after_rejections) != 0:
        failures.append("artifact repeat/insufficient guards changed inventory or balance")
    with connect() as conn:
        ts = now_iso()
        conn.execute(
            "INSERT INTO asset_transactions(asset_key,amount,reason,created_at) VALUES ('spirit_stone',30,'法器等级自检',?)",
            (ts,),
        )
        conn.execute(
            """
            INSERT INTO inventory_items(
                item_key,item_type,quantity,level,equipped,acquired_at,updated_at
            ) VALUES ('measuring_ruler','artifact',1,1,0,?,?)
            """,
            (ts, ts),
        )
        conn.commit()
    _check_response(
        failures,
        "scalable artifact upgrade",
        client.post("/world/artifacts/qingxin_slip/upgrade"),
    )
    _check_response(
        failures,
        "static artifact upgrade guard",
        client.post("/world/artifacts/measuring_ruler/upgrade"),
    )
    with connect() as conn:
        qingxin_level = conn.execute(
            "SELECT level FROM inventory_items WHERE item_key='qingxin_slip'"
        ).fetchone()["level"]
        ruler_level = conn.execute(
            "SELECT level FROM inventory_items WHERE item_key='measuring_ruler'"
        ).fetchone()["level"]
        stone_after_upgrades = conn.execute(
            "SELECT COALESCE(SUM(amount),0) n FROM asset_transactions WHERE asset_key='spirit_stone'"
        ).fetchone()["n"]
    if (
        int(qingxin_level) != 2
        or int(ruler_level) != 1
        or int(stone_after_upgrades) != 20
    ):
        failures.append("artifact levels charged for an effect that does not scale")
    if "作用已完整" not in client.get("/world").text:
        failures.append("static artifact did not explain that no upgrade was needed")

    plan_text = """# 自检近期计划
> 用真实交付驱动复盘
## 修炼任务
- [进阶] 建立证据边界判断能力 | 验收：完成一张命题—反例—验证表 | 工作区：EG 实验
## Day 1 | 证据链
- [重点] 检查一条科研判断 | 20min | 999XP | 交付：一张证据卡 | 关联修炼：建立证据边界判断能力
## Day 2 | 修正
- [可选] 写出反例 | 15min | 888经验 | 交付：反例与验证办法
"""
    _check_response(
        failures,
        "plan import",
        client.post("/plans/import", data={"plan_text": plan_text}),
    )
    with connect() as conn:
        plan = conn.execute(
            "SELECT * FROM study_plans WHERE name='自检近期计划' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        mission = conn.execute(
            "SELECT * FROM daily_missions WHERE plan_id=? AND day_index=1 ORDER BY id LIMIT 1",
            (plan["id"],),
        ).fetchone() if plan else None
        cultivation_task = conn.execute(
            "SELECT * FROM quests WHERE title='建立证据边界判断能力' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not plan or not mission:
        failures.append("plan import did not create the expected mission")
        return
    if not cultivation_task or int(mission["quest_id"] or 0) != int(cultivation_task["id"]):
        failures.append("plan import did not keep cultivation and daily tasks separate but linked")
    if int(mission["xp"]) != 6:
        failures.append("imported XP was not ignored in favor of the fixed duration rule")
    if "999XP" in plan["source_text"] or "888经验" in plan["source_text"]:
        failures.append("custom reward fields were not removed from the normalized plan text")
    if plan["status"] != "active":
        failures.append("imported plan was not activated")

    copied_plan = """电化学资产化入门计划
用5天形成可复用的电化学资产
修炼任务
• [进阶] 建立电化学最小概念与计算资产 | 验收：概念表与计算模板
第一天｜量纲起点
• [重点] 完成 Q-I-V-C-E-P 概念图 | 45分钟 | 交付：概念图
第二天｜CV 判读
• [可选] 判读一张 CV 曲线 | 20min | 交付：三句话判读
"""
    copied_response = client.post("/plans/import", data={"plan_text": copied_plan})
    _check_response(failures, "rendered-copy plan import", copied_response)
    with connect() as conn:
        copied = conn.execute(
            "SELECT * FROM study_plans WHERE name='电化学资产化入门计划' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        active_count = conn.execute(
            "SELECT COUNT(*) n FROM study_plans WHERE status='active'"
        ).fetchone()["n"]
    if not copied or copied["status"] != "active" or active_count != 1:
        failures.append("rendered-copy import did not become the only active plan")
    elif "/daily" not in str(copied_response.url):
        failures.append("successful plan import did not enter the daily page")

    activate_response = client.post(f"/plans/{plan['id']}/activate")
    _check_response(failures, "archived plan activation", activate_response)
    with connect() as conn:
        restored = conn.execute(
            "SELECT status FROM study_plans WHERE id=?", (plan["id"],)
        ).fetchone()
        active_count = conn.execute(
            "SELECT COUNT(*) n FROM study_plans WHERE status='active'"
        ).fetchone()["n"]
    if not restored or restored["status"] != "active" or active_count != 1:
        failures.append("archived plan activation did not switch atomically")

    invalid_activation = client.post("/plans/999999999/activate")
    _check_response(failures, "invalid plan activation", invalid_activation)
    with connect() as conn:
        still_active = conn.execute(
            "SELECT id FROM study_plans WHERE status='active'"
        ).fetchall()
    if [int(row["id"]) for row in still_active] != [int(plan["id"])]:
        failures.append("invalid activation changed the current plan")

    with connect() as conn:
        project_workspace_ids = [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM workspaces WHERE active=1 ORDER BY sort_order,id LIMIT 2"
            )
        ]
    project_response = client.post(
        "/projects/new",
        data={
            "title": "自检证据闸门课题",
            "research_question": "压实改变电子通路后，离子可达性是否成为限制步骤？",
            "rationale": "区分电子连通、离子可达与界面储能。",
            "target_outcome": "形成可复核的电子—离子失配证据链。",
            "success_criteria": "至少 3 批重复，主结论由阻抗与电容两类证据共同支持。",
            "current_state": "已有一组预实验 CV。",
            "constraints_text": "样品和仪器时间有限。",
            "search_query": "",
            "workspace_ids": project_workspace_ids,
        },
        follow_redirects=False,
    )
    _check_response(failures, "research project create", project_response, 303)
    with connect() as conn:
        project = conn.execute(
            "SELECT * FROM research_projects WHERE title='自检证据闸门课题' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        milestones = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                (project["id"],),
            )
        ] if project else []
        linked_workspaces = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_workspaces WHERE project_id=? ORDER BY is_primary DESC,workspace_id",
                (project["id"],),
            )
        ] if project else []
    if not project or len(milestones) != 5:
        failures.append("research project did not initialize five evidence gates")
    else:
        if len(linked_workspaces) != 2 or sum(int(item["is_primary"]) for item in linked_workspaces) != 1:
            failures.append("research project did not keep multiple linked workspaces with one primary")
        active_gates = [item for item in milestones if item["status"] == "active"]
        if len(active_gates) != 1:
            failures.append("research project did not initialize exactly one active gate")
        project_page = client.get(f"/projects/{project['id']}")
        _check_response(failures, "research project page", project_page)
        for marker in ("证据闸门", "Go / Revise / Stop", "联网查找相关先例", "Crossref"):
            if marker not in project_page.text:
                failures.append(f"research project page missed: {marker}")

        first = milestones[0]
        _check_response(
            failures,
            "empty project gate rejection",
            client.post(
                f"/projects/{project['id']}/milestones/{first['id']}/save",
                data={
                    "title": first["title"],
                    "criterion": first["criterion"],
                    "deliverable": first["deliverable"],
                    "status": "passed",
                },
            ),
        )
        with connect() as conn:
            unchanged = conn.execute(
                "SELECT status FROM project_milestones WHERE id=?", (first["id"],)
            ).fetchone()
        if not unchanged or unchanged["status"] != "active":
            failures.append("an evidence gate passed without evidence or a decision")

        _check_response(
            failures,
            "evidence-backed project gate",
            client.post(
                f"/projects/{project['id']}/milestones/{first['id']}/save",
                data={
                    "title": first["title"],
                    "criterion": first["criterion"],
                    "deliverable": first["deliverable"],
                    "status": "passed",
                    "evidence": "已保存一页问题定义卡并写明对照与边界。",
                    "decision": "Go：问题已可检验。",
                },
            ),
        )
        with connect() as conn:
            gate_states = [
                row["status"]
                for row in conn.execute(
                    "SELECT status FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                    (project["id"],),
                )
            ]
        if gate_states[:2] != ["passed", "active"]:
            failures.append("passing one project gate did not activate the next gate")

        _check_response(
            failures,
            "project precedent save",
            client.post(
                f"/projects/{project['id']}/cases/save",
                data={
                    "provider": "Crossref",
                    "external_id": "10.1234/self-test",
                    "title": "Evidence-gated research",
                    "authors": "Lin Qiu",
                    "publication_year": "2025",
                    "source": "Research Methods",
                    "doi": "10.1234/self-test",
                    "url": "https://doi.org/10.1234/self-test",
                    "cited_by": "7",
                    "relation": "baseline",
                },
            ),
        )
        _check_response(
            failures,
            "project evidence update",
            client.post(
                f"/projects/{project['id']}/updates/new",
                data={
                    "update_type": "evidence",
                    "summary": "科学问题已被压缩为一个可检验命题。",
                    "evidence": "问题定义卡",
                    "next_action": "筛选三篇直接先例。",
                    "confidence": "65",
                },
            ),
        )
        project_plan = client.get(f"/projects/{project['id']}/plan")
        _check_response(failures, "project short-plan bridge", project_plan)
        for marker in ("三日推进计划", "导入并立即进入 Day 1", "自检证据闸门课题"):
            if marker not in project_plan.text:
                failures.append(f"project short-plan bridge missed: {marker}")
        if "    - [重点]" in project_plan.text:
            failures.append("project short-plan rendered daily tasks as Markdown code blocks")

        _check_response(
            failures,
            "project-linked quick mission",
            client.post(
                f"/plans/{plan['id']}/missions/new",
                data={
                    "day_index": "1",
                    "category": "重点",
                    "title": "把课题证据链连回当天",
                    "deliverable": "一张关联卡",
                    "duration_minutes": "25",
                    "workspace_id": project_workspace_ids[0],
                    "project_id": str(project["id"]),
                },
            ),
        )
        with connect() as conn:
            linked_mission = conn.execute(
                """
                SELECT workspace_id,project_id FROM daily_missions
                WHERE title='把课题证据链连回当天' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if (
            not linked_mission
            or int(linked_mission["workspace_id"] or 0) != int(project_workspace_ids[0])
            or int(linked_mission["project_id"] or 0) != int(project["id"])
        ):
            failures.append("quick mission did not preserve workspace and project links")

        _check_response(
            failures,
            "AI collaboration return",
            client.post(
                "/assistant/save",
                data={
                    "title": "自检 AI 回存",
                    "summary": "当前证据只支持相关性，尚不足以声明因果。",
                    "evidence": "一张散点图和一组对照。",
                    "next_action": "补做一组控制变量实验。",
                    "destination": "both",
                    "workspace_id": project_workspace_ids[0],
                    "project_id": str(project["id"]),
                    "mode": "data",
                },
            ),
        )
        with connect() as conn:
            ai_entry = conn.execute(
                "SELECT id FROM entries WHERE title='自检 AI 回存'"
            ).fetchone()
            ai_update = conn.execute(
                """
                SELECT id FROM project_updates
                WHERE project_id=? AND summary LIKE '当前证据只支持相关性%'
                """,
                (project["id"],),
            ).fetchone()
            ai_egg = conn.execute(
                "SELECT unlocked FROM easter_eggs WHERE egg_key='ai_handoff'"
            ).fetchone()
        if not ai_entry or not ai_update or not ai_egg or not int(ai_egg["unlocked"]):
            failures.append("AI collaboration result did not return to knowledge and project state")
        with connect() as conn:
            scoped_state = current_state(
                conn,
                workspace_id=int(project_workspace_ids[0]),
                project_id=int(project["id"]),
            )
        if [int(item["id"]) for item in scoped_state["workspaces"]] != [
            int(project_workspace_ids[0])
        ]:
            failures.append("AI collaboration leaked unrelated workspaces into a scoped prompt")
        before_invalid = 0
        with connect() as conn:
            before_invalid = conn.execute(
                "SELECT COUNT(*) n FROM project_updates WHERE summary='不应保存的无效课题记录'"
            ).fetchone()["n"]
        _check_response(
            failures,
            "AI collaboration invalid project guard",
            client.post(
                "/assistant/save",
                data={
                    "title": "无效课题自检",
                    "summary": "不应保存的无效课题记录",
                    "destination": "project",
                    "project_id": "999999",
                    "mode": "project",
                },
            ),
        )
        with connect() as conn:
            after_invalid = conn.execute(
                "SELECT COUNT(*) n FROM project_updates WHERE summary='不应保存的无效课题记录'"
            ).fetchone()["n"]
        if int(after_invalid) != int(before_invalid):
            failures.append("AI collaboration accepted a nonexistent project destination")

    _check_response(
        failures,
        "delivery",
        client.post(
            f"/daily/missions/{mission['id']}/deliver",
            data={
                "view_day": "1",
                "note": "已提交证据卡。",
                "review_text": "科研判断必须说明证据、适用边界和可能反例。因果关系需要可验证，而不是只看相关性。",
                "folder_paths": "[]",
            },
        ),
    )
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with connect() as conn:
        delivery = conn.execute(
            "SELECT * FROM mission_deliveries WHERE mission_id=? ORDER BY id DESC LIMIT 1",
            (mission["id"],),
        ).fetchone()
        source = conn.execute(
            "SELECT * FROM review_sources WHERE source_type='mission_delivery' AND source_id=?",
            (delivery["id"],),
        ).fetchone() if delivery else None
        if source:
            conn.execute("UPDATE review_sources SET source_date=? WHERE id=?", (yesterday, source["id"]))
            conn.commit()
    if not delivery or not source:
        failures.append("delivery did not create a review source")
        return

    _check_response(
        failures,
        "knowledge attachment upload",
        client.post(
            "/upload",
            data={
                "title": "自检知识条目",
                "kind": "note",
                "domain": "电化学",
                "tags": "证据,自检",
                "summary": "用于验证 Markdown、JSON 与附件的一键导出。",
            },
            files={"files": ("evidence.txt", b"evidence-backed research note", "text/plain")},
        ),
    )
    _check_response(
        failures,
        "custom workspace create",
        client.post(
            "/workspaces/new",
            data={
                "name": "自检专题空间",
                "icon": "测",
                "module": "knowledge",
                "description": "验证每个人可拥有不同工作区。",
            },
        ),
    )
    with connect() as conn:
        workspace = conn.execute(
            "SELECT * FROM workspaces WHERE name='自检专题空间' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not workspace:
        failures.append("custom workspace was not created")
    else:
        _check_response(
            failures,
            "workspace attachment upload",
            client.post(
                "/upload",
                data={
                    "kind": "note",
                    "domain": "电化学",
                    "summary": "仅属于自检专题空间。",
                    "workspace_id": str(workspace["id"]),
                },
                files={"files": ("workspace-note.txt", b"workspace scoped note", "text/plain")},
            ),
        )
        workspace_page = client.get(f"/workspaces/{workspace['id']}")
        _check_response(failures, "custom workspace open", workspace_page)
        if "workspace-note" not in workspace_page.text:
            failures.append("custom workspace did not show its assigned knowledge entry")

    with connect() as conn:
        default_workspaces = {
            row["workspace_key"]: dict(row)
            for row in conn.execute(
                """
                SELECT * FROM workspaces
                WHERE workspace_key IN (
                    'eg-lab','lammps-lab','dataset-lab','ml-lab','md-lab','comsol-lab'
                )
                """
            )
        }
    workspace_modules = {
        "eg-lab": "experiments",
        "lammps-lab": "simulations",
        "dataset-lab": "datasets",
        "ml-lab": "ml",
        "md-lab": "md",
        "comsol-lab": "comsol",
    }
    for workspace_key, module in workspace_modules.items():
        workspace_item = default_workspaces.get(workspace_key)
        if not workspace_item:
            failures.append(f"default {workspace_key} workspace was not initialized")
            continue
        if workspace_item["module"] != module:
            failures.append(f"default {workspace_key} workspace used the wrong module")
        response = client.get(f"/workspaces/{workspace_item['id']}")
        _check_response(failures, f"{module} workspace open", response)
        if workspace_item["name"] not in response.text:
            failures.append(f"{module} workspace did not open its personalized module page")

    eg_workspace = default_workspaces.get("eg-lab")
    ml_workspace = default_workspaces.get("ml-lab")
    if eg_workspace and ml_workspace:
        eg_tasks = client.get(f"/cultivation?workspace={eg_workspace['id']}")
        ml_tasks = client.get(f"/cultivation?workspace={ml_workspace['id']}")
        _check_response(failures, "workspace-scoped cultivation tasks", eg_tasks)
        _check_response(failures, "empty workspace-scoped cultivation tasks", ml_tasks)
        if "建立证据边界判断能力" not in eg_tasks.text:
            failures.append("workspace task component did not show the linked milestone")
        if "建立证据边界判断能力" in ml_tasks.text:
            failures.append("workspace task component leaked another workspace's milestone")
        if f'value="{eg_workspace["id"]}" selected' not in eg_tasks.text:
            failures.append("workspace task form did not preselect the active workspace")

    if ml_workspace:
        _check_response(
            failures,
            "ML workspace personalization save",
            client.post(
                f"/workspaces/{ml_workspace['id']}/save",
                data={
                    "name": "材料 ML",
                    "icon": "模",
                    "module": "ml",
                    "description": "以实验数据验证模型。",
                    "objective": "建立带数据谱系和外部验证的模型卡。",
                    "workflow": "锁定数据版本\n记录特征管线\n比较指标\n实验外部验证",
                    "tools": ["datasets", "notes", "folders", "tasks"],
                    "accent": "amber",
                    "sort_order": "45",
                    "active": "1",
                    "pinned_home": "1",
                },
            ),
        )
        with connect() as conn:
            personalized_ml = conn.execute(
                "SELECT * FROM workspaces WHERE id=?",
                (ml_workspace["id"],),
            ).fetchone()
        if not personalized_ml:
            failures.append("personalized ML workspace disappeared")
        else:
            saved_workflow = json.loads(personalized_ml["workflow_json"])
            saved_tools = json.loads(personalized_ml["toolset_json"])
            if (
                personalized_ml["name"] != "材料 ML"
                or personalized_ml["accent"] != "amber"
                or personalized_ml["objective"] != "建立带数据谱系和外部验证的模型卡。"
                or saved_workflow != ["锁定数据版本", "记录特征管线", "比较指标", "实验外部验证"]
                or saved_tools != ["datasets", "notes", "folders", "tasks"]
                or int(personalized_ml["pinned_home"]) != 1
            ):
                failures.append("ML workspace personalization did not round-trip")
            personalized_page = client.get(f"/workspaces/{ml_workspace['id']}")
            _check_response(failures, "personalized ML workspace open", personalized_page)
            for marker in ("材料 ML", "建立带数据谱系", "锁定数据版本", "项目文件夹"):
                if marker not in personalized_page.text:
                    failures.append(f"personalized ML workspace missed: {marker}")

    experiment_workspace = default_workspaces.get("eg-lab")
    simulation_workspace = default_workspaces.get("lammps-lab")
    dataset_workspace = default_workspaces.get("dataset-lab")
    if experiment_workspace:
        _check_response(
            failures,
            "workspace experiment create",
            client.post(
                "/experiments/save",
                data={
                    "sample_id": "SELFTEST-EG-01",
                    "title": "工作区实验",
                    "workspace_id": str(experiment_workspace["id"]),
                },
            ),
        )
    if simulation_workspace:
        _check_response(
            failures,
            "workspace simulation create",
            client.post(
                "/simulations/new",
                data={
                    "case_name": "SELFTEST-LAMMPS-01",
                    "workspace_id": str(simulation_workspace["id"]),
                },
            ),
        )
    if dataset_workspace:
        _check_response(
            failures,
            "workspace dataset create",
            client.post(
                "/upload",
                data={
                    "title": "自检工作区数据集",
                    "kind": "dataset",
                    "workspace_id": str(dataset_workspace["id"]),
                },
                files={"files": ("selftest.csv", b"x,y\n1,2\n", "text/csv")},
            ),
        )
    with connect() as conn:
        if experiment_workspace and conn.execute(
            "SELECT COUNT(*) n FROM experiments WHERE sample_id='SELFTEST-EG-01' AND workspace_id=?",
            (experiment_workspace["id"],),
        ).fetchone()["n"] != 1:
            failures.append("experiment record was not scoped to its personal workspace")
        if simulation_workspace and conn.execute(
            "SELECT COUNT(*) n FROM simulations WHERE case_name='SELFTEST-LAMMPS-01' AND workspace_id=?",
            (simulation_workspace["id"],),
        ).fetchone()["n"] != 1:
            failures.append("simulation record was not scoped to its personal workspace")
        if dataset_workspace and conn.execute(
            "SELECT COUNT(*) n FROM entries WHERE title='自检工作区数据集' AND workspace_id=?",
            (dataset_workspace["id"],),
        ).fetchone()["n"] != 1:
            failures.append("dataset record was not scoped to its personal workspace")

    dashboard = client.get("/")
    _check_response(failures, "pending review dashboard", dashboard)
    if "到了提取时间" not in dashboard.text:
        failures.append("dashboard did not surface the pending review")
    for marker in (
        "mountain-gate", "今日一诗", "gate-dual-search", "搜知识库", "联网找论文",
        "gate-shortcuts", "home-workbench-dock", "home-continuity-strip",
        "data-living-scene", "gate-sun", "gate-birds",
        "LAMMPS", "数据集", "ML", "MD", "COMSOL", "英语学习",
    ):
        if marker not in dashboard.text:
            failures.append(f"light mountain-gate dashboard missed: {marker}")
    if 'formaction="http://testserver/search"' not in dashboard.text or 'formaction="http://testserver/discover"' not in dashboard.text:
        failures.append("homepage did not expose both local knowledge and online paper search")
    for removed_marker in ("dashboard-v2", "今日修行闭环", "今日论文卡", "每日一问", "闪念收集", "home-tool-dock"):
        if removed_marker in dashboard.text:
            failures.append(f"removed v2.0 dashboard block was still rendered: {removed_marker}")

    review_landing = client.get("/review")
    _check_response(failures, "review landing separation", review_landing)
    if 'value="beast"' in review_landing.text or 'value="tribulation"' in review_landing.text:
        failures.append("review page still contained independent challenge launch forms")
    trials_landing = client.get("/trials")
    _check_response(failures, "trials landing", trials_landing)
    for marker in ("灵光问答", "因果迷宫", "反证之境", "万象秘境", "五问雷劫"):
        if marker not in trials_landing.text:
            failures.append(f"independent trials page missed mode: {marker}")
    for mode, expected_count in {
        "quick": 3,
        "mechanism": 4,
        "counterexample": 4,
        "beast": 5,
    }.items():
        _check_response(
            failures,
            f"{mode} trial start",
            client.post("/review/start", data={"mode": mode}),
        )
        with connect() as conn:
            trial_session = conn.execute(
                "SELECT questions_json FROM review_sessions WHERE mode=? ORDER BY id DESC LIMIT 1",
                (mode,),
            ).fetchone()
        if not trial_session or len(json.loads(trial_session["questions_json"])) != expected_count:
            failures.append(f"{mode} trial did not create {expected_count} questions")

    _check_response(
        failures,
        "review start",
        client.post("/review/start", data={"mode": "yesterday"}),
    )
    with connect() as conn:
        session = conn.execute(
            "SELECT * FROM review_sessions WHERE mode='yesterday' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not session:
        failures.append("review start did not create a session")
        return
    questions = json.loads(session["questions_json"])
    if len(questions) != 3:
        failures.append(f"review session expected 3 questions, got {len(questions)}")
    for index, question in enumerate(questions):
        answer = f"我的回答依据是：{question.get('evidence', '')}"
        _check_response(
            failures,
            f"review answer {index}",
            client.post(
                f"/review/sessions/{session['id']}/answer",
                data={"question_index": str(index), "answer": answer},
            ),
        )
    _check_response(
        failures,
        "review self rating",
        client.post(
            f"/review/sessions/{session['id']}/rate",
            data={"question_index": "0", "self_rating": "partial"},
        ),
    )
    with connect() as conn:
        finished = conn.execute("SELECT status FROM review_sessions WHERE id=?", (session["id"],)).fetchone()
        answers = conn.execute(
            "SELECT COUNT(*) n FROM review_answers WHERE session_id=?", (session["id"],)
        ).fetchone()["n"]
        rating = conn.execute(
            "SELECT self_rating,next_due FROM review_answers WHERE session_id=? AND question_index=0",
            (session["id"],),
        ).fetchone()
    if not finished or finished["status"] != "completed" or answers != len(questions):
        failures.append("review session did not complete")
    if not rating or rating["self_rating"] != "partial" or not rating["next_due"]:
        failures.append("review self rating was not scheduled")

    first_gate = TRIBULATION_GATES[0]
    target_xp = next(
        stage.threshold for stage in REALM_STAGES if stage.key == first_gate.to_key
    )
    with connect() as conn:
        missing_xp = max(0, target_xp - total_xp(conn))
        if missing_xp:
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("selftest_realm_setup", missing_xp, "只用于一次性迁移与雷劫自检", now_iso()),
            )
        conn.execute(
            """
            INSERT INTO inventory_items(
                item_key,item_type,quantity,level,equipped,acquired_at,updated_at
            ) VALUES ('tribulation_pill','pill',1,1,0,?,?)
            ON CONFLICT(item_key) DO UPDATE SET
                item_type='pill',quantity=quantity+1,updated_at=excluded.updated_at
            """,
            (now_iso(), now_iso()),
        )
        conn.commit()
    locked_realm = app.current_realm(target_xp)
    if not locked_realm["tribulation_required"]:
        failures.append("reaching a major threshold did not activate the tribulation gate")
    _check_response(
        failures,
        "tribulation start at breakthrough",
        client.post("/review/start", data={"mode": "tribulation"}),
    )
    with connect() as conn:
        tribulation_session = conn.execute(
            "SELECT * FROM review_sessions WHERE mode='tribulation' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not tribulation_session:
        failures.append("eligible tribulation did not create a session")
    else:
        tribulation_questions = json.loads(tribulation_session["questions_json"])
        for index, question in enumerate(tribulation_questions):
            _check_response(
                failures,
                f"tribulation answer {index}",
                client.post(
                    f"/review/sessions/{tribulation_session['id']}/answer",
                    data={
                        "question_index": str(index),
                        "answer": str(question.get("evidence", "")),
                    },
                ),
            )
        with connect() as conn:
            attempt = conn.execute(
                "SELECT * FROM realm_tribulations WHERE session_id=?",
                (tribulation_session["id"],),
            ).fetchone()
        if not attempt or attempt["status"] != "passed":
            failures.append("high-evidence tribulation answers did not unlock the gate")
        elif app.current_realm(total_xp())["key"] != first_gate.to_key:
            failures.append("passed tribulation did not advance the visible realm")

    _check_response(
        failures,
        "special task generate",
        client.post(
            "/alchemy/tasks/generate",
            data={"difficulty": "2", "focus": "检查科研判断的证据边界"},
        ),
    )
    with connect() as conn:
        task = conn.execute(
            "SELECT * FROM special_tasks WHERE status='offered' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not task:
        failures.append("special task generation failed")
        return
    _check_response(
        failures,
        "special task accept",
        client.post(f"/alchemy/tasks/{task['id']}/accept"),
    )
    _check_response(
        failures,
        "special task complete",
        client.post(
            f"/alchemy/tasks/{task['id']}/complete",
            data={
                "evidence": "完成了一张命题—反例—验证办法表。",
                "review_text": "反例用于检查结论的适用边界；验证办法必须对应可观测量。",
            },
        ),
    )
    with connect() as conn:
        completed = conn.execute("SELECT status FROM special_tasks WHERE id=?", (task["id"],)).fetchone()
        herb = conn.execute("SELECT quantity FROM herb_inventory WHERE grade=2").fetchone()
        ts = now_iso()
        conn.execute(
            """
            INSERT INTO herb_inventory(grade,herb_name,quantity,updated_at)
            VALUES (1,'青露草',2,?)
            ON CONFLICT(grade) DO UPDATE SET quantity=quantity+2,updated_at=excluded.updated_at
            """,
            (ts,),
        )
        conn.commit()
    if not completed or completed["status"] != "completed" or not herb or herb["quantity"] < 1:
        failures.append("special task did not award the expected herb")

    _check_response(failures, "alchemy craft", client.post("/alchemy/craft/clarity_pill"))
    with connect() as conn:
        pill = conn.execute(
            "SELECT quantity FROM inventory_items WHERE item_key='clarity_pill'"
        ).fetchone()
    if not pill or pill["quantity"] < 1:
        failures.append("alchemy did not create a clarity pill")
    _check_response(
        failures,
        "alchemy pill use",
        client.post("/alchemy/pills/clarity_pill/use"),
    )

    realm_names = "\n".join(["mortal=见习研究者", "body_early=证据学徒"])
    nav_labels = "\n".join(["dashboard=科研台", "alchemy=实验炼丹房", "group_workspaces=我的实验空间"])
    nav_layout = navigation_layout()
    nav_groups = {group["key"]: group for group in nav_layout}
    nav_layout = [
        nav_groups["system"],
        nav_groups["cultivation"],
        nav_groups["knowledge"],
        nav_groups["workspaces"],
        nav_groups["growth"],
    ]
    cultivation_items = {
        item["key"]: item for item in nav_groups["cultivation"]["items"]
    }
    nav_groups["cultivation"]["items"] = [
        cultivation_items["daily"],
        cultivation_items["dashboard"],
        *[
            item
            for item in nav_groups["cultivation"]["items"]
            if item["key"] not in {"daily", "dashboard"}
        ],
    ]
    for item in nav_groups["system"]["items"]:
        if item["key"] in {"assistant", "settings"}:
            item["visible"] = False
    _check_response(
        failures,
        "personalization settings",
        client.post(
            "/settings",
            data={
                "site_name": "自检科研系统",
                "researcher_name": "自检用户",
                "domains": "电化学\n未分类",
                "ai_mode": "offline",
                "ai_endpoint": "http://127.0.0.1:11434/api/generate",
                "ai_model": "qwen2.5:7b",
                "realm_names": realm_names,
                "nav_labels": nav_labels,
                "nav_layout": json.dumps(nav_layout, ensure_ascii=False),
                "review_popup": "1",
                "poem_pool": "问渠那得清如许？为有源头活水来。——朱熹\n纸上得来终觉浅，绝知此事要躬行。——陆游",
                "ui_accent": "cobalt",
                "ui_density": "spacious",
                "ui_scene": "aurora",
                "ui_motion": "lively",
                "ui_geometry": "orbital",
                "ui_font_scale": "large",
                "ui_home_effect": "constellation",
                "ui_home_motto": "让证据连接下一步",
            },
        ),
    )
    if get_setting("site_name") != "自检科研系统":
        failures.append("site personalization was not saved")
    saved_nav = json.loads(get_setting("nav_labels", "{}"))
    if saved_nav.get("dashboard") != "科研台" or saved_nav.get("alchemy") != "实验炼丹房":
        failures.append("navigation personalization was not saved")
    saved_layout = json.loads(get_setting("nav_layout", "[]"))
    if (
        not saved_layout
        or saved_layout[0].get("key") != "system"
        or saved_layout[1].get("items", [{}])[0].get("key") != "daily"
    ):
        failures.append("navigation order was not saved")
    rendered_custom_nav = client.get("/daily").text
    if rendered_custom_nav.find('data-nav-section="system"') > rendered_custom_nav.find('data-nav-section="cultivation"'):
        failures.append("custom navigation group order was not rendered")
    if 'data-nav-item="assistant"' in rendered_custom_nav or 'data-nav-item="settings"' in rendered_custom_nav:
        failures.append("hidden navigation items were still rendered in the sidebar")
    if "调整导航" not in rendered_custom_nav:
        failures.append("navigation recovery link disappeared after hiding settings")

    avatar_buffer = io.BytesIO()
    Image.new("RGB", (96, 96), "#8b654d").save(avatar_buffer, format="PNG")
    _check_response(
        failures,
        "avatar upload",
        client.post(
            "/profile/avatar",
            data={
                "avatar_action": "upload",
                "avatar_choice": "研",
                "avatar_custom": "",
            },
            files={"avatar": ("avatar.png", avatar_buffer.getvalue(), "image/png")},
        ),
    )
    avatar_file = get_setting("avatar_file", "")
    if not avatar_file or not (PROFILE_DIR / avatar_file).is_file():
        failures.append("avatar upload did not create a portable profile image")

    package_response = client.get("/online/personalization/export")
    _check_response(failures, "personalization export", package_response)
    try:
        package = json.loads(package_response.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        package = {}
        failures.append("personalization export was not valid JSON")
    if package.get("format") != "research-cultivation-personalization-v8":
        failures.append("personalization export did not use the v8 format")
    exported_theme = package.get("theme", {})
    if (
        exported_theme.get("motion") != "lively"
        or exported_theme.get("geometry") != "orbital"
        or exported_theme.get("font_scale") != "large"
        or exported_theme.get("home_effect") != "constellation"
    ):
        failures.append("personalization export missed expanded local appearance")
    if len(package.get("theme", {}).get("realm_names", {})) != 39:
        failures.append("personalization export did not include the full realm map")
    if not package.get("workspaces"):
        failures.append("personalization export did not include workspace definitions")
    exported_layout = package.get("theme", {}).get("nav_layout", [])
    if not exported_layout or exported_layout[0].get("key") != "system":
        failures.append("personalization export did not include navigation layout")
    exported_ml = next(
        (
            workspace
            for workspace in package.get("workspaces", [])
            if workspace.get("workspace_key") == "ml-lab"
        ),
        {},
    )
    if (
        exported_ml.get("name") != "材料 ML"
        or exported_ml.get("objective") != "建立带数据谱系和外部验证的模型卡。"
        or exported_ml.get("workflow") != ["锁定数据版本", "记录特征管线", "比较指标", "实验外部验证"]
        or exported_ml.get("tools") != ["datasets", "notes", "folders", "tasks"]
    ):
        failures.append("personalization export missed composable ML workspace fields")
    if not package.get("avatar_image", {}).get("data_base64"):
        failures.append("personalization export did not include the uploaded avatar")
    if "hub_api_token" in json.dumps(package, ensure_ascii=False):
        failures.append("personalization export leaked the hub token field")
    set_setting("site_name", "临时改名")
    _check_response(
        failures,
        "personalization import",
        client.post(
            "/online/personalization/import",
            files={
                "file": (
                    "personalization.json",
                    json.dumps(package, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        ),
    )
    if get_setting("site_name") != "自检科研系统":
        failures.append("personalization import did not restore the site name")

    overflow_package = json.loads(json.dumps(package, ensure_ascii=False))
    for workspace_item in overflow_package.get("workspaces", []):
        workspace_item["active"] = 1
        workspace_item["pinned_home"] = 1
    _check_response(
        failures,
        "personalization home pin limit",
        client.post(
            "/online/personalization/import",
            files={
                "file": (
                    "personalization-overflow.json",
                    json.dumps(overflow_package, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        ),
    )
    with connect() as conn:
        imported_pin_count = conn.execute(
            "SELECT COUNT(*) n FROM workspaces WHERE active=1 AND pinned_home=1"
        ).fetchone()["n"]
    if imported_pin_count != 6:
        failures.append("personalization import did not enforce the six-workspace home limit")

    legacy_package = {
        "format": "research-cultivation-personalization-v1",
        "theme": {
            "realm_names": [
                "旧包凡人", "旧包炼气一层", "旧包炼气中期", "旧包炼气圆满",
                "旧包筑基初期", "旧包筑基圆满", "旧包金丹初成", "旧包金丹圆满",
                "旧包元婴", "旧包化神", "旧包炼虚", "旧包合体", "旧包大乘", "旧包渡劫",
            ],
            "nav_labels": {"dashboard": "旧包首页"},
        },
        "profile": package.get("profile", {}),
    }
    _check_response(
        failures,
        "legacy personalization import",
        client.post(
            "/online/personalization/import",
            files={
                "file": (
                    "personalization-v1.json",
                    json.dumps(legacy_package, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        ),
    )
    imported_legacy_realms = json.loads(get_setting("realm_names", "{}"))
    imported_legacy_nav = json.loads(get_setting("nav_labels", "{}"))
    if len(imported_legacy_realms) != 39 or imported_legacy_realms.get("mortal") != "旧包凡人":
        failures.append("legacy realm labels were not expanded without losing custom names")
    if imported_legacy_nav.get("dashboard") != "旧包首页" or "group_workspaces" not in imported_legacy_nav:
        failures.append("legacy navigation labels were not merged with new customizable items")
    _check_response(
        failures,
        "restore current personalization",
        client.post(
            "/online/personalization/import",
            files={
                "file": (
                    "personalization-v8.json",
                    json.dumps(package, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
            },
        ),
    )

    portable_knowledge = client.get("/knowledge/export")
    _check_response(failures, "portable knowledge export", portable_knowledge)
    try:
        with zipfile.ZipFile(io.BytesIO(portable_knowledge.content)) as archive:
            names = archive.namelist()
        if not any(name.startswith("entries/") and name.endswith(".md") for name in names):
            failures.append("knowledge export did not create Markdown entries")
        if not any(name.startswith("attachments/") for name in names):
            failures.append("knowledge export did not include original attachments")
        if "records/research_projects.json" not in names:
            failures.append("knowledge export did not include research projects")
        if "records/project_workspaces.json" not in names:
            failures.append("knowledge export did not include project-workspace relationships")
        if "records/career_moments.json" not in names:
            failures.append("knowledge export did not include career milestones")
    except zipfile.BadZipFile:
        failures.append("portable knowledge export was not a valid ZIP")

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (USER_CONFIG_DIR / "self-test-layout.json").write_text(
        '{"workspace_layout":"self-test"}\n',
        encoding="utf-8",
    )

    complete_backup = client.get("/backup")
    _check_response(failures, "complete backup with user config", complete_backup)
    try:
        with zipfile.ZipFile(io.BytesIO(complete_backup.content)) as archive:
            backup_names = set(archive.namelist())
        if "user_config/self-test-layout.json" not in backup_names:
            failures.append("complete backup missed separated user configuration")
    except zipfile.BadZipFile:
        failures.append("complete backup was not a valid ZIP")

    portable_system = client.get("/portable")
    _check_response(failures, "separated portable system export", portable_system)
    try:
        with zipfile.ZipFile(io.BytesIO(portable_system.content)) as archive:
            names = set(archive.namelist())
        required_portable_files = {
            "ResearchCultivationOS/portable.flag",
            "ResearchCultivationOS/PORTABLE_MANIFEST.json",
            "ResearchCultivationOS/user_data/instance/research_os.db",
            "ResearchCultivationOS/user_data/user_config/self-test-layout.json",
        }
        if not required_portable_files.issubset(names):
            failures.append("portable system export missed its separated data layout")
        if any(
            name.startswith("ResearchCultivationOS/instance/")
            or name.startswith("ResearchCultivationOS/storage/")
            or "/.git/" in name
            for name in names
        ):
            failures.append("portable system export leaked development or legacy data paths")
        if not any(
            name.startswith("ResearchCultivationOS/user_data/storage/uploads/")
            for name in names
        ):
            failures.append("portable system export missed uploaded research files")
    except zipfile.BadZipFile:
        failures.append("portable system export was not a valid ZIP")


def main() -> None:
    integration = "--integration" in sys.argv
    client = TestClient(app.app)
    pages = [
        "/", "/cultivation", "/daily", "/review", "/trials", "/achievements", "/retreat", "/alchemy", "/world", "/profile", "/plans",
        "/projects", "/career", "/foundation", "/assistant", "/notes/new", "/library", "/search", "/discover", "/workspaces", "/settings", "/online",
    ]
    failures = []
    _check_plan_copy_parser(failures)
    _check_scholar_parser(failures)
    for page in pages:
        response = client.get(page)
        _check_response(failures, page, response)
    daily_navigation = client.get("/daily")
    for marker in (
        'data-nav-item="literature_report"',
        'data-nav-item="review"',
        'data-nav-item="projects"',
        'data-nav-item="library"',
    ):
        if marker not in daily_navigation.text:
            failures.append(f"1.0 navigation missed: {marker}")
    if "nav-more" in daily_navigation.text:
        failures.append("desktop navigation still rendered a collapsed tools section")
    settings_navigation = client.get("/settings")
    for marker in (
        'data-nav-layout-editor',
        'data-nav-reset',
        'data-nav-item-key="workspace_shortcuts"',
        "调整导航",
    ):
        if marker not in settings_navigation.text:
            failures.append(f"navigation editor missed: {marker}")
    normalized_layout = normalize_nav_layout(
        [
            {
                "key": "system",
                "items": [
                    {"key": "assistant", "visible": False},
                    {"key": "assistant", "visible": True},
                    {"key": "unknown", "visible": True},
                ],
            },
            {"key": "unknown", "items": []},
        ]
    )
    if (
        normalized_layout[0]["key"] != "system"
        or normalized_layout[0]["items"][0] != {
            "key": "assistant",
            "visible": False,
        }
        or len(
            [
                item
                for group in normalized_layout
                for item in group["items"]
                if item["key"] == "assistant"
            ]
        )
        != 1
    ):
        failures.append("navigation layout normalization did not reject duplicates or unknown keys")
    capabilities = client.get("/api/sync/capabilities")
    _check_response(failures, "sync capability interface", capabilities)
    if capabilities.status_code == 200:
        capability_data = capabilities.json()
        if capability_data.get("active_backend", {}).get("enabled"):
            failures.append("scale-ready sync interface was not disabled by default")
        if capability_data.get("data_policy", {}).get("cloud_v2_implemented"):
            failures.append("reserved cloud backend incorrectly reported as implemented")
        reliability = capability_data.get("reliability_policy", {})
        expected_reliability = {
            "auto_timeout_seconds": 1.5,
            "failure_threshold": 3,
            "circuit_pause_seconds": 300,
            "dead_letter_attempts": 6,
            "respects_retry_after": True,
        }
        for key, expected in expected_reliability.items():
            if reliability.get(key) != expected:
                failures.append(f"sync reliability policy mismatch for {key}")
    knowledge_export = client.get("/knowledge/export")
    _check_response(failures, "knowledge export", knowledge_export)
    try:
        with zipfile.ZipFile(io.BytesIO(knowledge_export.content)) as archive:
            names = set(archive.namelist())
            if not {
                "README.md",
                "manifest.json",
                "knowledge.json",
                "records/research_projects.json",
                "records/project_workspaces.json",
                "records/project_milestones.json",
                "records/project_cases.json",
                "records/project_updates.json",
                "records/career_moments.json",
            }.issubset(names):
                failures.append("knowledge export missed its portable index files")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != "research-cultivation-knowledge-v5":
                failures.append("knowledge export did not use the multi-workspace v5 format")
    except zipfile.BadZipFile:
        failures.append("knowledge export was not a valid ZIP")
    with connect() as conn:
        required_tables = {
            "mission_deliveries", "mission_delivery_files", "asset_transactions", "inventory_items",
            "player_profile", "easter_eggs", "track_growth", "online_sync_queue", "online_sync_cache",
            "review_sources", "review_sessions", "review_session_sources", "review_answers",
            "review_snoozes", "realm_tribulations", "special_tasks", "herb_inventory",
            "workspaces", "research_projects", "project_milestones", "project_cases",
            "project_updates", "project_workspaces", "career_moments",
        }
        found = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required_tables - found)
        if missing:
            failures.append("missing tables: " + ", ".join(missing))
        if conn.execute("SELECT COUNT(*) n FROM study_plans").fetchone()["n"] < 1:
            failures.append("no study plan")
        if conn.execute("SELECT COUNT(*) n FROM daily_missions").fetchone()["n"] < 1:
            failures.append("no daily missions")
        default_keys = {
            row["workspace_key"]
            for row in conn.execute(
                """
                SELECT workspace_key FROM workspaces
                WHERE workspace_key IN (
                    'eg-lab','lammps-lab','dataset-lab','ml-lab','md-lab','comsol-lab'
                )
                """
            )
        }
        if default_keys != {
            "eg-lab", "lammps-lab", "dataset-lab", "ml-lab", "md-lab", "comsol-lab",
        }:
            failures.append("six default workspaces were not initialized")
        workspace_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(workspaces)")
        }
        expected_workspace_columns = {
            "pinned_home", "objective", "workflow_json", "toolset_json",
        }
        if not expected_workspace_columns.issubset(workspace_columns):
            failures.append("workspace personalization columns were not migrated")
        pinned_defaults = conn.execute(
            """
            SELECT COUNT(*) n FROM workspaces
            WHERE workspace_key IN (
                'eg-lab','lammps-lab','dataset-lab','ml-lab','md-lab','comsol-lab'
            ) AND pinned_home=1
            """
        ).fetchone()["n"]
        if pinned_defaults != 6:
            failures.append("six default workspaces were not pinned on first initialization")
        old_identity = conn.execute(
            "SELECT COUNT(*) n FROM settings WHERE key='researcher_name' AND trim(value)='准研一修士'"
        ).fetchone()["n"]
        old_profile_identity = conn.execute(
            "SELECT COUNT(*) n FROM player_profile WHERE id=1 AND trim(display_name)='准研一修士'"
        ).fetchone()["n"]
        if old_identity or old_profile_identity:
            failures.append("legacy default identity was not migrated from 准研一修士 to 修士")
    if get_setting("portable_version") != APP_VERSION:
        failures.append(f"portable version was not migrated to {APP_VERSION}")
    if len(REALM_STAGES) != 39:
        failures.append(f"realm system expected 39 stages, got {len(REALM_STAGES)}")
    requirements = [stage.required_xp for stage in REALM_STAGES[1:]]
    if len(requirements) != len(set(requirements)):
        failures.append("realm XP requirements were not unique per stage")
    if not TRIBULATION_GATES:
        failures.append("no major breakthrough gates were defined")
    else:
        first_gate = TRIBULATION_GATES[0]
        target_xp = next(
            stage.threshold for stage in REALM_STAGES if stage.key == first_gate.to_key
        )
        locked = realm_state(target_xp, passed_gate_keys=())
        unlocked = realm_state(target_xp, passed_gate_keys={first_gate.key})
        if not locked["tribulation_required"] or locked["key"] != first_gate.from_key:
            failures.append("Golden-Core-plus breakthrough was not locked before tribulation")
        if unlocked["key"] != first_gate.to_key:
            failures.append("passed tribulation did not unlock the target realm")
    if integration:
        _run_integration(client, failures)
    if failures:
        print("SELF TEST FAILED")
        for failure in failures:
            print("-", failure)
        raise SystemExit(1)
    print("SELF TEST PASS")
    if integration:
        print("Plans, career memory, artifact transactions, deliveries, review, alchemy and personalization are ready.")
    else:
        print(f"Core pages, v{APP_VERSION} living mountain gate, career compass, workspaces, sync guardrails and portable data are ready.")


if __name__ == "__main__":
    main()
