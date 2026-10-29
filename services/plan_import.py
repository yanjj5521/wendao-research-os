from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.progression import fixed_daily_xp


@dataclass
class MissionSpec:
    category: str
    title: str
    description: str = ""
    deliverable: str = ""
    duration_minutes: int = 30
    xp: int = 0
    optional: bool = False
    cultivation_title: str = ""
    workspace_name: str = ""
    project_name: str = ""

    def __post_init__(self) -> None:
        self.duration_minutes = max(5, min(int(self.duration_minutes or 30), 240))
        # XP is a system rule. Imported or manually supplied values never win.
        self.xp = fixed_daily_xp(self.duration_minutes)


@dataclass
class CultivationTaskSpec:
    title: str
    description: str = ""
    deliverable: str = ""
    difficulty: int = 1
    workspace_name: str = ""


@dataclass
class DaySpec:
    index: int
    title: str
    missions: list[MissionSpec] = field(default_factory=list)


@dataclass
class PlanSpec:
    name: str
    description: str
    days: list[DaySpec]
    source_text: str
    cultivation_tasks: list[CultivationTaskSpec] = field(default_factory=list)


CATEGORY_ALIASES = {
    "主线": "重点",
    "核心": "重点",
    "重点": "重点",
    "必做": "重点",
    "每日": "重点",
    "main": "重点",
    "工具": "工具",
    "md": "工具",
    "ml": "工具",
    "支线": "工具",
    "补给": "补给",
    "英语": "补给",
    "文献": "补给",
    "可选": "可选",
    "选做": "可选",
    "加练": "可选",
    "bonus": "可选",
}

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _to_int(value: str, default: int) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else default


def _day_number(value: str) -> int | None:
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if not value or any(char not in CHINESE_DIGITS and char != "十" for char in value):
        return None
    if "十" not in value:
        return CHINESE_DIGITS.get(value)
    before, after = value.split("十", 1)
    tens = CHINESE_DIGITS.get(before, 1) if before else 1
    ones = CHINESE_DIGITS.get(after, 0) if after else 0
    return tens * 10 + ones


def _plain_line(raw: str) -> tuple[int, str]:
    """Return Markdown heading level and copy-friendly visible text."""
    line = (raw or "").strip().replace("\u00a0", " ").replace("\u3000", " ")
    heading = re.match(r"^(#{1,6})\s*(.*?)\s*#*\s*$", line)
    level = len(heading.group(1)) if heading else 0
    visible = heading.group(2) if heading else line
    # ChatGPT's copy button normally keeps Markdown, while mouse-selection may
    # leave only rendered text. Removing lightweight emphasis supports both.
    visible = visible.replace("**", "").replace("__", "").strip()
    return level, visible


def _day_heading(value: str, heading_level: int = 0) -> tuple[int, str] | None:
    patterns = [
        r"^day\s*([0-9一二两三四五六七八九十〇零]+)\s*(?:天|日)?\s*(.*)$",
        r"^第\s*([0-9一二两三四五六七八九十〇零]+)\s*(?:天|日)\s*(.*)$",
    ]
    if heading_level >= 2:
        patterns.append(r"^([0-9一二两三四五六七八九十〇零]+)\s*(?:天|日)?\s*(.*)$")
    for pattern in patterns:
        match = re.match(pattern, value, re.I)
        if not match:
            continue
        index = _day_number(match.group(1))
        if index is None or index < 1:
            return None
        title = re.sub(r"^[|｜·—–\-:：]\s*", "", match.group(2).strip())
        return index, title or f"第 {index} 日"
    return None


def _task_line(value: str) -> tuple[str, str] | None:
    task = re.match(
        r"^(?:[-*+•▪▫‣]\s*|(?:\d+)[.)、]\s*)(?:\[([^\]]+)\])?\s*(.+)$",
        value,
    )
    if not task:
        task = re.match(r"^\[([^\]]+)\]\s*(.+)$", value)
    if not task:
        return None
    return (task.group(1) or "").strip(), task.group(2).replace("**", "").replace("__", "").strip()


def _clean_category(value: str) -> tuple[str, bool]:
    key = (value or "重点").strip().lower()
    category = CATEGORY_ALIASES.get(key, value.strip() or "重点")
    return category, category == "可选"


def parse_plan_text(text: str) -> PlanSpec:
    """Parse a forgiving AI-friendly Markdown plan.

    Recommended format:
      # 本周近期计划
      > 说明
      ## 修炼任务
      - [进阶] 独立判读CV曲线 | 验收：完成一份盲测判读记录
      ## Day 1 | 电化学起点
      - [重点] 电荷—电流—电势 | 45min | 交付：概念图

    Extra fields can be added as `说明：...` or `交付：...` segments.
    """
    source = (text or "").strip()
    if not source:
        raise ValueError("计划文案不能为空")

    name = "AI导入计划"
    name_found = False
    description_parts: list[str] = []
    days: list[DaySpec] = []
    cultivation_tasks: list[CultivationTaskSpec] = []
    current: DaySpec | None = None
    section = ""

    for raw in source.splitlines():
        heading_level, line = _plain_line(raw)
        if not line:
            continue
        if line.startswith("```"):
            continue
        if line.startswith(">"):
            description_parts.append(line.lstrip("> ").strip())
            continue
        if re.fullmatch(r"(修炼任务|能力目标|成果里程碑)\s*[：:]?", line, re.I):
            current = None
            section = "cultivation"
            continue
        heading = _day_heading(line, heading_level)
        if heading:
            index, title = heading
            current = DaySpec(index=index, title=title)
            days.append(current)
            section = "daily"
            continue
        if heading_level == 1:
            name = line or name
            name_found = True
            continue
        task = _task_line(line)
        if not task:
            if not section and not name_found:
                name = line
                name_found = True
            elif not section:
                description_parts.append(line)
            continue
        if section == "cultivation":
            marker = (task[0] or "小成").strip().lower()
            difficulty = {
                "1": 1, "小成": 1, "基础": 1, "入门": 1,
                "2": 2, "进阶": 2, "熟练": 2,
                "3": 3, "突破": 3, "挑战": 3,
            }.get(marker, 1)
            parts = [part.strip() for part in re.split(r"\s*[|｜]\s*", task[1]) if part.strip()]
            if not parts:
                continue
            description = ""
            deliverable = ""
            workspace_name = ""
            for part in parts[1:]:
                if re.match(r"^(交付|交付物|验收|证据|达成标准)[：:]", part):
                    deliverable = re.split(r"[：:]", part, maxsplit=1)[1].strip()
                elif re.match(r"^(说明|描述)[：:]", part):
                    description = re.split(r"[：:]", part, maxsplit=1)[1].strip()
                elif re.match(r"^(工作区|领域)[：:]", part):
                    workspace_name = re.split(r"[：:]", part, maxsplit=1)[1].strip()
                elif "xp" in part.lower() or "修为" in part or "经验" in part:
                    # Cultivation rewards are also determined by the fixed difficulty table.
                    continue
                else:
                    description = f"{description} {part}".strip()
            cultivation_tasks.append(
                CultivationTaskSpec(
                    title=parts[0],
                    description=description,
                    deliverable=deliverable,
                    difficulty=difficulty,
                    workspace_name=workspace_name,
                )
            )
            continue
        if current is None:
            continue
        category, optional = _clean_category(task[0] or "重点")
        parts = [part.strip() for part in re.split(r"\s*[|｜]\s*", task[1]) if part.strip()]
        if not parts:
            continue
        title = parts[0]
        duration = 30
        deliverable = ""
        detail = ""
        cultivation_title = ""
        workspace_name = ""
        project_name = ""
        for part in parts[1:]:
            lower = part.lower()
            if "min" in lower or "分钟" in part:
                duration = _to_int(part, duration)
            elif "xp" in lower or "修为" in part or "经验" in part:
                # Accepted only for backward compatibility; deliberately ignored.
                continue
            elif re.match(r"^(交付|交付物|证据|产出)[：:]", part):
                deliverable = re.split(r"[：:]", part, maxsplit=1)[1].strip()
            elif re.match(r"^(关联修炼|修炼任务|关联目标)[：:]", part):
                cultivation_title = re.split(r"[：:]", part, maxsplit=1)[1].strip()
            elif re.match(r"^(工作区|领域)[：:]", part):
                workspace_name = re.split(r"[：:]", part, maxsplit=1)[1].strip()
            elif re.match(r"^(课题|关联课题|项目)[：:]", part):
                project_name = re.split(r"[：:]", part, maxsplit=1)[1].strip()
            elif re.match(r"^(说明|提示|内容)[：:]", part):
                detail = re.split(r"[：:]", part, maxsplit=1)[1].strip()
            else:
                detail = f"{detail} {part}".strip()
        current.missions.append(
            MissionSpec(
                category=category,
                title=title,
                description=detail,
                deliverable=deliverable,
                duration_minutes=max(5, min(duration, 240)),
                optional=optional,
                cultivation_title=cultivation_title,
                workspace_name=workspace_name,
                project_name=project_name,
            )
        )

    merged_days: dict[int, DaySpec] = {}
    for day in days:
        if day.index in merged_days:
            merged_days[day.index].missions.extend(day.missions)
        else:
            merged_days[day.index] = day
    days = sorted(merged_days.values(), key=lambda item: item.index)
    if not days:
        raise ValueError("没有识别到日标题。请保留 Day 1 / 第1天；可直接粘贴带或不带 # 的完整计划。")
    # A copied answer can occasionally skip or repeat a visible number. Recent
    # plans are always stored as a contiguous sequence so “下一天” never opens
    # an empty page.
    for normalized_index, day in enumerate(days, start=1):
        day.index = normalized_index
        if not day.missions:
            day.missions.append(MissionSpec(category="重点", title=day.title, duration_minutes=30))
    return PlanSpec(
        name=name,
        description=" ".join(description_parts).strip() or "由 AI 文案导入，可随时继续修改。",
        days=days,
        source_text=source,
        cultivation_tasks=cultivation_tasks,
    )


def render_plan_text(plan: PlanSpec) -> str:
    lines = [f"# {plan.name}", f"> {plan.description}", ""]
    if plan.cultivation_tasks:
        labels = {1: "小成", 2: "进阶", 3: "突破"}
        lines.append("## 修炼任务")
        for task in plan.cultivation_tasks:
            parts = [f"- [{labels.get(task.difficulty, '小成')}] {task.title}"]
            if task.deliverable:
                parts.append(f"验收：{task.deliverable}")
            if task.workspace_name:
                parts.append(f"领域：{task.workspace_name}")
            if task.description:
                parts.append(f"说明：{task.description}")
            lines.append(" | ".join(parts))
        lines.append("")
    for day in plan.days:
        lines.append(f"## Day {day.index} | {day.title}")
        for mission in day.missions:
            parts = [
                f"- [{mission.category}] {mission.title}",
                f"{mission.duration_minutes}min",
            ]
            if mission.deliverable:
                parts.append(f"交付：{mission.deliverable}")
            if mission.cultivation_title:
                parts.append(f"关联修炼：{mission.cultivation_title}")
            if mission.workspace_name:
                parts.append(f"领域：{mission.workspace_name}")
            if mission.project_name:
                parts.append(f"项目：{mission.project_name}")
            if mission.description:
                parts.append(f"说明：{mission.description}")
            lines.append(" | ".join(parts))
        lines.append("")
    return "\n".join(lines).strip()


CORE_TOPICS = [
    ("电荷、电流、电势与能量", "画出 Q–I–V–C–E–P 的关系图，并写对单位。"),
    ("Nernst 方程与电极电势", "能用自己的话解释浓度为什么会改变电势。"),
    ("电化学双电层", "画出电极/电解液界面的离子与电荷分布。"),
    ("EDLC 与赝电容", "用储能机制、曲线特征和材料类型完成对比。"),
    ("传质：扩散、迁移与对流", "指出水泥基器件里最可能受限的传质环节。"),
    ("电荷转移动力学", "理解过电位、极化与反应速率的关系。"),
    ("水系电解液的稳定窗口", "列出析氢、析氧与副反应的判断信号。"),
    ("CV 测试原理", "能解释扫速、积分面积和曲线形状分别意味着什么。"),
    ("CV 曲线判读", "用三句话判断矩形度、极化和赝电容迹象。"),
    ("GCD 与电容计算", "完成一组质量、面积和体积电容计算。"),
    ("EIS 入门", "认出高频截距、半圆和低频斜线的含义。"),
    ("两电极与三电极体系", "画出二者连接方式并说明各自回答的问题。"),
    ("归一化与公平比较", "建立质量、面积、体积基准的选择清单。"),
    ("倍率、自放电与循环", "形成器件性能评价的最小指标表。"),
    ("水泥储能、发电与热储能", "完成水泥能源分类图，避免概念混用。"),
    ("水泥孔结构与含水状态", "解释孔、连通性和含水量为何影响离子输运。"),
    ("碳—水泥导电网络", "区分电子贯通、离子可达与力学骨架。"),
    ("膨胀石墨的结构特征", "解释片层、膨胀结构、压实和润湿的利弊。"),
    ("电子路径与离子路径", "写出高导电但低电容的三种可能原因。"),
    ("加工—结构—输运—性能", "画出你的核心研究因果链。"),
    ("第一轮实验矩阵", "只保留能检验核心假设的变量和对照。"),
    ("器件几何与集流体", "固定面积、厚度、集流方式和计算口径。"),
    ("浸泡与预处理", "制定电解液浸润是否到位的观察与测试标准。"),
    ("预实验电压窗口", "形成从保守窗口逐步扩展的测试流程。"),
    ("CV–GCD–EIS 测试顺序", "写出一套不容易污染结论的测试顺序。"),
    ("实验数据模板与质控", "建立样品编号、原始数据、异常和结论字段。"),
    ("论文 Figure 叙事", "拆解一篇核心论文的 Why–How–Result–Meaning。"),
    ("MD 与实验问题对齐", "只提出一个 MD 能真正回答的局部机制问题。"),
    ("ML 数据集字段", "建立 Processing–Structure–Transport–Performance 字段表。"),
    ("30天整合与下一阶段", "形成一张领域地图和下一轮三项最小行动。"),
]

MD_TASKS = [
    "整理 LJ 基线目录，确认输入、日志、轨迹和运行命令",
    "读懂 LAMMPS 输入脚本中的 units、atom_style 与 boundary",
    "从 log.lammps 找出步数、温度、能量与警告",
    "用 OVITO 查看一段轨迹并记录一个结构观察",
    "理解最小化、NVT、NPT 和 production 的分工",
    "练习 RDF 或 MSD 的物理含义，不急着套到项目",
    "为一个案例写可复现 README",
    "列出 EG/水/离子局部模型所需的最小组成",
    "区分力场、边界条件与系综带来的假设",
    "把一个模拟输出对应到实验可解释量",
    "检查单位、时间步长和采样间隔",
    "设计一次最小参数扫描而不是大批量盲跑",
    "建立模拟案例命名与版本规范",
    "写出 MD 暂时不能回答的宏观问题",
    "形成 MD 下一阶段路线卡",
]

ML_TASKS = [
    "用 Python 读取一个 CSV 并查看字段、缺失值与单位",
    "建立实验数据字典：字段名、类型、单位、来源",
    "练习 pandas 的筛选、分组和导出",
    "区分训练集、验证集和测试集",
    "理解特征、标签、数据泄漏与样本量",
    "画一张变量相关性图并避免因果误读",
    "建立 EG 实验数据的空白模板",
    "把异常值处理规则写进数据说明",
    "理解回归指标 MAE、RMSE 与 R²",
    "用一个小数据集跑通线性回归基线",
    "记录模型版本、特征版本与随机种子",
    "理解交叉验证适合解决什么问题",
    "列出可解释 ML 需要回答的科研问题",
    "把 MD 描述符与实验字段分层管理",
    "形成 ML 下一阶段路线卡",
]


def build_default_plan() -> PlanSpec:
    days = [
        DaySpec(
            index=1,
            title="确定眼前最值得推进的问题",
            missions=[
                MissionSpec("重点", "写下当前最想解决的一个科研问题", "把问题缩小到能在近期推进的尺度。", "问题—依据—下一步三行卡片", 35, 20),
                MissionSpec("工具", "整理一个正在使用的文件或案例", "只补齐复现所需信息，不重复无目的运行。", "README、参数表或文件清单", 30, 15),
                MissionSpec("可选", "让 AI 检查问题是否可证伪", "带上你的原始判断和已有证据。", "一条修正后的问题", 15, 6, True),
            ],
        ),
        DaySpec(
            index=2,
            title="把理解变成证据",
            missions=[
                MissionSpec("重点", "解释昨天问题中的关键机制", "不用追求长篇，写清变量、关系和边界。", "一张概念图或200字机制卡", 45, 22),
                MissionSpec("补给", "局部精读一段真正相关的文献", "只读摘要、图注或直接相关段落。", "一张论文证据卡", 30, 15),
            ],
        ),
        DaySpec(
            index=3,
            title="决定下一段近期计划",
            missions=[
                MissionSpec("重点", "复盘这两天的交付并选择下一步", "区分已解决、仍不确定和暂不值得做。", "继续／停止／验证清单", 35, 20),
                MissionSpec("工具", "生成并导入下一份3–7天计划", "允许完全替换当前安排，不制造补课债务。", "一份可导入计划文本", 20, 10),
            ],
        ),
    ]
    plan = PlanSpec(
        name="起步近期计划 · 3天",
        description="先用三天建立“问题—交付—复盘”闭环，之后随时换成更贴合现状的新计划。",
        days=days,
        source_text="",
    )
    plan.source_text = render_plan_text(plan)
    return plan
