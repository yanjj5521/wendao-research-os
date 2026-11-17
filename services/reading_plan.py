"""SPECS → 碳水泥阅读计划：把用户的阅读规范做成系统内置数据。

原则文本与条目内容按用户给定清单**原文**入库；系统只提供结构、进度与记录骨架，
不预判任何一篇的结论——具体公式、参数与证据以原文为准。
"""

from __future__ import annotations

import json
from typing import Any

from core.database import connect, now_iso

PLAN_SLUG = "specs-carbon-cement"
PLAN_TITLE = "SPECS 方法 → 碳水泥：阅读清单与验收"
PLAN_PRINCIPLE = (
    "按 01—08 顺序阅读：原始方法 → 拟合修正 → 理论验证 → 多孔碳改进 → 应用与模型拓展 → 碳水泥体系。"
    "先通读摘要、图和结论，再精读方法与关键推导；完成交付物后再进入下一篇。"
    "阅读任务不预判任何一篇的结论，具体公式、参数与证据须回原文确认。"
    "红线：不要直接把拟合时间常数等同于孔径；未统一质量或体积基准时，不比较性能。"
)

# 统一记录的五栏（每篇必填），字段名 → 提示语
UNIFIED_FIELDS: tuple[tuple[str, str], ...] = (
    ("question", "1. 研究问题与一句话结论（附原文页码、图号）"),
    ("setup", "2. 样品、装置、测试条件和数据处理流程"),
    ("formulas", "3. 关键公式：符号、单位、假设、拟合范围"),
    ("evidence", "4. 最有说服力的两项证据，以及一项未解决的问题"),
    ("transfer", "5. 对碳水泥体系可迁移的内容、需要重做的验证"),
)

NOTES_KINDS: tuple[tuple[str, str], ...] = (
    ("point", "要点"),
    ("insight", "心得"),
    ("question", "问题"),
)

# (序号, 论文, 阅读任务, 阅读目标与交付物) —— 用户清单原文
PLAN_ITEMS: tuple[tuple[int, str, str, str], ...] = (
    (1, "01 Dupont & Donne (2015)：SPECS 原始方法",
     "画出电位阶跃及电流响应；逐项整理拟合公式、符号、单位与假设；记录测试条件及数据处理步骤。",
     "能解释从电流响应到储能参数的分析链条；交付一页原理笔记和公式表。"),
    (2, "02 Forghani & Donne (2019)：SPECS 拟合程序修正",
     "对照 01，定位拟合流程、初值、时间窗口及约束的变化；区分算法改动与物理解释变化；核查误差评价。",
     "明确旧方法何时可能失效及如何修正；交付新旧流程对照表和拟合检查清单。"),
    (3, "03 Munteshari et al. (2019)：SPECS 理论验证",
     "梳理理论模型、边界条件与验证方式；追踪参数与物理量的对应关系；列出适用和失效条件。",
     "判断拟合结果是否具有物理可解释性；交付“假设—验证证据—适用范围”表。"),
    (4, "04 Wang et al. (2021)：多孔碳改进 SPECS",
     "对照前述方法，标出针对多孔碳的修改；梳理孔结构、电解液与响应时间之间的解释；检查与其他表征的交叉验证。",
     "理解复杂孔结构对参数解释的影响；交付方法改进表及孔结构与动力学关联图。"),
    (5, "05 Wang et al. (2023)：Nature Communications",
     "沿研究问题、实验设计、SPECS 分析、关键图、结论逐项阅读；提取原文真正支持的机制证据，标明推断和局限。",
     "把方法转化为研究问题的分析工具；交付关键图证据表和可迁移实验方案草稿。"),
    (6, "06 Zhou et al. (2024)：三维 SPECS 模型",
     "对照低维模型，整理几何结构、控制方程、边界条件与输入参数；检查数值结果如何与实验比较及参数是否可辨识。",
     "识别三维结构带来的解释增量与计算代价；交付模型对照表和所需结构数据清单。"),
    (7, "07 Chanut et al. (2023)：碳水泥超级电容器",
     "梳理配方、制备、碳网络、孔隙与电解液作用；核对电容和能量密度的归一化基准；整理力学与储能性能的关系。",
     "建立碳水泥材料结构到储能性能的证据链；交付结构示意图和性能口径表。"),
    (8, "08 Stefaniuk et al. (2025)：碳水泥三维结构与储能",
     "对照 07，核查三维结构表征及储能提升证据；分清电子网络和离子通道；评估 06 的模型迁移需要哪些测量。",
     "形成可验证的碳水泥 SPECS 研究设想；交付三条研究假设及各自的实验、对照与判据。"),
)

STAGES: tuple[tuple[str, tuple[int, int, int], str], ...] = (
    ("stage1", (1, 2, 3), "能说明 SPECS 测什么、怎样拟合、参数解释依赖什么假设"),
    ("stage2", (4, 5, 6), "能比较各版本，并提出适用于目标材料的测试与建模方案"),
    ("stage3", (7, 8), "能区分导电网络、可达孔隙与离子输运的作用，提出有对照实验的研究问题"),
)

_REMINDERS: tuple[str, ...] = (
    "不要直接把拟合时间常数等同于孔径。",
    "未统一质量或体积基准时，不比较性能。",
)


def ensure_default_plan() -> dict[str, Any] | None:
    """首次访问时种入默认计划；已存在则原样返回。内容为用户清单原文。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM reading_plans WHERE slug=?", (PLAN_SLUG,)).fetchone()
        if row:
            return dict(row)
        cursor = conn.execute(
            "INSERT INTO reading_plans(slug,title,principle,created_at) VALUES (?,?,?,?)",
            (PLAN_SLUG, PLAN_TITLE, PLAN_PRINCIPLE, now_iso()),
        )
        plan_id = int(cursor.lastrowid)
        ts = now_iso()
        for seq, label, task, deliverable in PLAN_ITEMS:
            conn.execute(
                """INSERT INTO reading_plan_items
                   (plan_id,seq,label,task,deliverable,status,updated_at)
                   VALUES (?,?,?,?,?,'todo',?)""",
                (plan_id, seq, label, task, deliverable, ts),
            )
        conn.commit()
        return {"id": plan_id, "slug": PLAN_SLUG, "title": PLAN_TITLE, "principle": PLAN_PRINCIPLE}


def plan_payload() -> dict[str, Any] | None:
    """计划页需要的全部数据：条目、关联工作台记录、统一记录完成度、阶段验收。"""
    with connect() as conn:
        plan_row = conn.execute("SELECT * FROM reading_plans WHERE slug=?", (PLAN_SLUG,)).fetchone()
        if not plan_row:
            return None
        plan = dict(plan_row)
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM reading_plan_items WHERE plan_id=? ORDER BY seq", (plan["id"],)
        )]
        notes_rows = [dict(r) for r in conn.execute(
            """SELECT n.*, w.title AS work_title FROM reading_notes n
               LEFT JOIN reading_works w ON w.id=n.work_id ORDER BY n.id DESC LIMIT 200"""
        )]
    unified_by_item: dict[int, dict[str, str]] = {}
    for item in items:
        work_id = item.get("reading_work_id")
        if not work_id:
            continue
        with connect() as conn:
            work = conn.execute(
                "SELECT id,title,unified_json FROM reading_works WHERE id=?", (work_id,)
            ).fetchone()
        if not work:
            continue
        try:
            unified = json.loads(work["unified_json"] or "{}")
        except json.JSONDecodeError:
            unified = {}
        unified = {k: str(unified.get(k, "")).strip() for k, _hint in UNIFIED_FIELDS}
        unified_by_item[item["id"]] = unified

    def item_state(item: dict[str, Any]) -> dict[str, Any]:
        unified = unified_by_item.get(item["id"], {})
        filled = sum(1 for k, _ in UNIFIED_FIELDS if unified.get(k))
        return {
            **item,
            "linked": bool(item.get("reading_work_id")),
            "unified_filled": filled,
            "unified_total": len(UNIFIED_FIELDS),
            "complete": item["status"] == "done" and filled == len(UNIFIED_FIELDS),
        }

    states = [item_state(item) for item in items]
    stages = []
    for stage_key, seqs, goal in STAGES:
        members = [st for st in states if st["seq"] in seqs]
        stages.append({
            "key": stage_key,
            "goal": goal,
            "members": members,
            "done": bool(members) and all(m["complete"] for m in members),
            "progress": int(sum(1 for m in members if m["complete"]) / len(members) * 100) if members else 0,
        })
    return {
        "plan": plan,
        "entries": states,
        "item_unified": unified_by_item,
        "stages": stages,
        "unified_fields": UNIFIED_FIELDS,
        "note_kinds": NOTES_KINDS,
        "reminders": _REMINDERS,
        "notes": notes_rows,
        "open_questions": [
            {"work_title": n["work_title"], "content": n["content"], "created_at": n["created_at"]}
            for n in notes_rows if n["kind"] == "question"
        ],
    }


def link_item(item_id: int, work_id: int | None) -> None:
    with connect() as conn:
        if work_id is None:
            conn.execute(
                "UPDATE reading_plan_items SET reading_work_id=NULL,updated_at=? WHERE id=?",
                (now_iso(), item_id),
            )
        else:
            conn.execute(
                "UPDATE reading_plan_items SET reading_work_id=?,updated_at=? WHERE id=?",
                (work_id, now_iso(), item_id),
            )
            conn.execute("UPDATE reading_works SET plan_item_id=? WHERE id=?", (item_id, work_id))
        conn.commit()


def set_status(item_id: int, status: str) -> bool:
    if status not in {"todo", "reading", "done"}:
        return False
    with connect() as conn:
        conn.execute(
            "UPDATE reading_plan_items SET status=?,updated_at=? WHERE id=?",
            (status, now_iso(), item_id),
        )
        conn.commit()
    return True


def save_unified(work_id: int, values: dict[str, str]) -> int:
    """保存五栏统一记录；返回已填写栏数。"""
    payload = {}
    for key, _hint in UNIFIED_FIELDS:
        payload[key] = (values.get(key, "") or "").strip()[:2000]
    with connect() as conn:
        conn.execute(
            "UPDATE reading_works SET unified_json=?,updated_at=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), now_iso(), work_id),
        )
        conn.commit()
    return sum(1 for v in payload.values() if v)


def load_unified(work_id: int) -> dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT unified_json FROM reading_works WHERE id=?", (work_id,)).fetchone()
    if not row:
        return {k: "" for k, _ in UNIFIED_FIELDS}
    try:
        data = json.loads(row["unified_json"] or "{}")
    except json.JSONDecodeError:
        data = {}
    return {k: str(data.get(k, "")).strip() for k, _ in UNIFIED_FIELDS}


def add_note(work_id: int, kind: str, content: str) -> bool:
    if kind not in {k for k, _ in NOTES_KINDS}:
        return False
    content = content.strip()[:1000]
    if not content:
        return False
    with connect() as conn:
        conn.execute(
            "INSERT INTO reading_notes(work_id,kind,content,created_at) VALUES (?,?,?,?)",
            (work_id, kind, content, now_iso()),
        )
        conn.commit()
    return True
