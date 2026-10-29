"""打包自检：验证发布版应用的关键链路。

从 run_local.py 抽离，职责单一：用 TestClient 走一遍核心页面、
导航编辑、知识条目、计划导入、项目闸门与生涯节点，任何一步失败
都以非零码退出。入口：`python run_local.py --self-check`。
"""

from __future__ import annotations

import os
import sys
from datetime import date

from core.runtime_paths import DATA_ROOT


def safe_print(message: str) -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream)
    except UnicodeEncodeError:
        encoding = stream.encoding or "ascii"
        escaped = message.encode(encoding, errors="backslashreplace").decode(encoding)
        print(escaped, file=stream)


def run_packaged_self_check(web_app) -> None:
    from fastapi.testclient import TestClient

    with TestClient(web_app) as client:
        gate_code = os.getenv("RESEARCH_OS_ACCESS_CODE", "").strip()
        if gate_code:
            # 启用了访问门禁时，自检需要先持有门禁 Cookie。
            client.post("/gate", data={"code": gate_code, "next": "/"})
        from core.database import connect
        from core.version import APP_VERSION

        with TestClient(web_app) as client:
            gate_code = os.getenv("RESEARCH_OS_ACCESS_CODE", "").strip()
            if gate_code:
                # 启用了访问门禁时，自检需要先持有门禁 Cookie。
                client.post("/gate", data={"code": gate_code, "next": "/"})
            for path in (
                "/",
                "/projects",
                "/career",
                "/world",
                "/workspaces",
                "/settings",
                "/online",
                "/literature-report",
                "/api/stats",
            ):
                response = client.get(path)
                if response.status_code != 200:
                    raise SystemExit(
                        f"Packaged self-check failed: {path} returned HTTP {response.status_code}."
                    )
            homepage = client.get("/").text
            if "gate-dual-search" not in homepage or "data-living-scene" not in homepage:
                raise SystemExit("Packaged self-check failed: the homepage template is incomplete.")
            settings_page = client.get("/settings").text
            if (
                "data-nav-layout-editor" not in settings_page
                or "data-nav-reset" not in settings_page
                or "调整导航" not in settings_page
            ):
                raise SystemExit(
                    "Packaged self-check failed: the navigation editor is incomplete."
                )
            world = client.get("/world")
            if world.status_code != 200 or "购入 · 12 灵石" not in world.text:
                raise SystemExit("Packaged self-check failed: the starter artifact is not affordable.")
            client.post("/world/artifacts/qingxin_slip/buy")
            client.post("/world/artifacts/qingxin_slip/buy")
            client.post("/world/artifacts/measuring_ruler/buy")
            with connect() as conn:
                starter_artifact = conn.execute(
                    "SELECT COUNT(*) n FROM inventory_items WHERE item_key='qingxin_slip'"
                ).fetchone()["n"]
                unavailable_artifact = conn.execute(
                    "SELECT 1 FROM inventory_items WHERE item_key='measuring_ruler'"
                ).fetchone()
                stone_balance = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) n FROM asset_transactions "
                    "WHERE asset_key='spirit_stone'"
                ).fetchone()["n"]
            if starter_artifact != 1 or unavailable_artifact or int(stone_balance) != 0:
                raise SystemExit(
                    "Packaged self-check failed: artifact purchase safeguards are incomplete."
                )
            career_focus = client.post(
                "/career/focus",
                data={
                    "phase": "validate",
                    "focus": "形成可以复核的证据链。",
                    "boundary": "不把单次相关性当作机制。",
                    "success_signal": "至少两类独立证据支持同一判断。",
                    "review_date": "",
                },
            )
            career_moment = client.post(
                "/career/moments/new",
                data={
                    "moment_type": "decision",
                    "title": "打包自检生涯节点",
                    "summary": "验证长期科研记忆可写入。",
                    "evidence": "打包后表单链路",
                    "project_id": "0",
                    "occurred_on": date.today().isoformat(),
                },
            )
            if career_focus.status_code != 200 or career_moment.status_code != 200:
                raise SystemExit("Packaged self-check failed: career compass forms are incomplete.")
            with connect() as conn:
                phase = conn.execute(
                    "SELECT value FROM settings WHERE key='career_phase'"
                ).fetchone()
                moment = conn.execute(
                    "SELECT 1 FROM career_moments WHERE title='打包自检生涯节点'"
                ).fetchone()
            if not phase or phase["value"] != "validate" or not moment:
                raise SystemExit("Packaged self-check failed: career memory was not persisted.")
            plans_page = client.get("/plans")
            if "planImportForm" not in plans_page.text or "导入并立即进入 Day 1" not in plans_page.text:
                raise SystemExit("Packaged self-check failed: plan import controls are incomplete.")
            with connect() as conn:
                original = conn.execute(
                    "SELECT id FROM study_plans WHERE status='active' ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            copied_plan = """电化学资产化入门计划
用5天形成可复用的电化学资产
修炼任务
• [进阶] 建立电化学最小概念与计算资产 | 验收：概念表与计算模板
第一天｜量纲起点
• [重点] 完成 Q-I-V-C-E-P 概念图 | 45分钟 | 交付：概念图
第二天｜CV 判读
• [可选] 判读一张 CV 曲线 | 20min | 交付：三句话判读
"""
            imported = client.post("/plans/import", data={"plan_text": copied_plan})
            if imported.status_code != 200 or "电化学资产化入门计划" not in imported.text:
                raise SystemExit("Packaged self-check failed: a copied plan could not be imported.")
            with connect() as conn:
                active = conn.execute(
                    "SELECT id,name FROM study_plans WHERE status='active'"
                ).fetchall()
            if len(active) != 1 or active[0]["name"] != "电化学资产化入门计划":
                raise SystemExit("Packaged self-check failed: imported plan was not activated.")
            client.post("/plans/999999999/activate")
            with connect() as conn:
                after_invalid = conn.execute(
                    "SELECT id FROM study_plans WHERE status='active'"
                ).fetchall()
            if len(after_invalid) != 1 or int(after_invalid[0]["id"]) != int(active[0]["id"]):
                raise SystemExit("Packaged self-check failed: invalid activation changed the current plan.")
            if original:
                client.post(f"/plans/{original['id']}/activate")
                with connect() as conn:
                    restored = conn.execute(
                        "SELECT id FROM study_plans WHERE status='active'"
                    ).fetchall()
                if len(restored) != 1 or int(restored[0]["id"]) != int(original["id"]):
                    raise SystemExit("Packaged self-check failed: archived plan activation failed.")
            created = client.post(
                "/projects/new",
                data={
                    "title": "打包自检课题",
                    "research_question": "变量 A 是否在约束 C 下改变结果 B？",
                    "rationale": "验证课题推进的真实表单链路。",
                    "target_outcome": "形成可审查结论。",
                    "success_criteria": "至少三次重复且效应达到预设阈值。",
                    "current_state": "已有一组预实验。",
                    "constraints_text": "样品有限。",
                    "search_query": "",
                },
                follow_redirects=False,
            )
            if created.status_code != 303:
                raise SystemExit("Packaged self-check failed: a research project could not be created.")
            with connect() as conn:
                project = conn.execute(
                    "SELECT id FROM research_projects WHERE title='打包自检课题' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                gates = (
                    conn.execute(
                        "SELECT id,title,criterion,deliverable,status FROM project_milestones "
                        "WHERE project_id=? ORDER BY sort_order,id",
                        (project["id"],),
                    ).fetchall()
                    if project
                    else []
                )
            if not project or len(gates) != 5 or gates[0]["status"] != "active":
                raise SystemExit("Packaged self-check failed: project evidence gates are incomplete.")
            client.post(
                f"/projects/{project['id']}/milestones/{gates[0]['id']}/save",
                data={
                    "title": gates[0]["title"],
                    "criterion": gates[0]["criterion"],
                    "deliverable": gates[0]["deliverable"],
                    "status": "passed",
                },
            )
            with connect() as conn:
                rejected = conn.execute(
                    "SELECT status FROM project_milestones WHERE id=?", (gates[0]["id"],)
                ).fetchone()
            if not rejected or rejected["status"] != "active":
                raise SystemExit("Packaged self-check failed: an empty evidence gate was accepted.")
            client.post(
                f"/projects/{project['id']}/milestones/{gates[0]['id']}/save",
                data={
                    "title": gates[0]["title"],
                    "criterion": gates[0]["criterion"],
                    "deliverable": gates[0]["deliverable"],
                    "status": "passed",
                    "evidence": "问题卡已保存，对照与边界均已写明。",
                    "decision": "Go：进入先例检索。",
                },
            )
            with connect() as conn:
                states = [
                    row["status"]
                    for row in conn.execute(
                        "SELECT status FROM project_milestones WHERE project_id=? ORDER BY sort_order,id",
                        (project["id"],),
                    )
                ]
            if states[:2] != ["passed", "active"]:
                raise SystemExit("Packaged self-check failed: the next evidence gate was not activated.")
            project_plan = client.get(f"/projects/{project['id']}/plan")
            if project_plan.status_code != 200 or "导入并立即进入 Day 1" not in project_plan.text:
                raise SystemExit("Packaged self-check failed: the project plan bridge is incomplete.")
        safe_print(f"问道科研 {APP_VERSION} self-check PASS")
        safe_print(f"Data: {DATA_ROOT}")
        raise SystemExit(0)

    safe_print(f"问道科研 {APP_VERSION} self-check PASS")
    safe_print(f"Data: {DATA_ROOT}")
