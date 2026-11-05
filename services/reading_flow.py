"""论文三遍读与汇报：把"先抓骨架 → 再抠细节 → 最后叙事"变成可填的表。

设计取舍：
- 不自动抓 PDF、不调模型，避免"AI 替你读了"的错觉；
- 只做三件事：卡住节奏（三遍各要回答什么）、生成汇报骨架、提前准备问答；
- 所有内容都落在本机 ``reading_works`` 表，导出的 PPT 大纲是纯文本，可直接贴进幻灯片。
"""

from __future__ import annotations

import json
from typing import Any

STAGES: tuple[tuple[str, str, str], ...] = (
    ("pass1", "第一遍 · 速览", "5–10 分钟：只看标题、摘要、图表、结论、参考文献，决定读不读。"),
    ("pass2", "第二遍 · 抓骨架", "约 1 小时：跳过证明，抓研究问题、方法、实验、结果、局限。"),
    ("pass3", "第三遍 · 虚拟复现", "2–3 小时：逐行对公式与代码，找弱点；每周不超过一篇。"),
    ("ready", "可汇报", "字段齐了，能按六段式讲 15 分钟。"),
)

DECISIONS: tuple[tuple[str, str], ...] = (
    ("read", "细读"),
    ("archive", "存档"),
    ("drop", "不读"),
)

SCENARIOS: dict[str, dict[str, str]] = {
    "group": {"label": "组会文献汇报", "focus": "重点讲「这篇对我们课题有什么可复用」，方法细节可略。"},
    "class": {"label": "课堂精读", "focus": "重点讲方法细节与推导是否吃透，公式要能推。"},
    "review": {"label": "文献综述环节", "focus": "重点讲它和同方向其它工作的定位关系，单篇细节略讲。"},
    "defense": {"label": "开题 / 中期 / 答辩", "focus": "按「背景 → 内容与意义 → 进展 → 计划」讲，不是讲别人的论文。"},
}

QA_TREE: tuple[tuple[str, str, str], ...] = (
    ("value", "选题价值", "为什么选这篇 / 这个方向？"),
    ("novelty", "创新点", "它的创新在哪里？"),
    ("method", "方法合理性", "为什么用这个方法而不是别的？"),
    ("limits", "局限性", "它有哪些不足？"),
    ("inspire", "拓展启发", "对你自己的研究有什么启发？"),
)

# 第一遍要回答的"五个 C"
FIVE_C: tuple[tuple[str, str, str], ...] = (
    ("category", "Category", "什么类型的论文（方法 / 系统 / 综述 / 实证）"),
    ("context", "Context", "与哪些相关工作相关，接在哪条线上"),
    ("correctness", "Correctness", "假设看起来是否合理"),
    ("contribution", "Contribution", "主要贡献是什么"),
    ("clarity", "Clarity", "写得是否清晰"),
)

def empty_qa() -> dict[str, dict[str, str]]:
    return {key: {"question": question, "answer": ""} for key, _label, question in QA_TREE}


def parse_qa(raw: Any) -> dict[str, dict[str, str]]:
    base = empty_qa()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in base and isinstance(value, dict):
                base[key]["answer"] = str(value.get("answer", ""))[:1200]
                if value.get("question"):
                    base[key]["question"] = str(value["question"])[:160]
    return base


def dump_qa(answers: dict[str, str], existing: dict[str, dict[str, str]] | None = None) -> str:
    payload = parse_qa(existing or {})
    for key, text in answers.items():
        if key in payload:
            payload[key]["answer"] = (text or "").strip()[:1200]
    return json.dumps(payload, ensure_ascii=False)


def stage_label(stage: str) -> str:
    for key, label, _hint in STAGES:
        if key == stage:
            return label
    return STAGES[0][1]


def next_stage(stage: str) -> str:
    keys = [key for key, _label, _hint in STAGES]
    index = keys.index(stage) if stage in keys else 0
    return keys[min(index + 1, len(keys) - 1)]


def checklist(work: dict[str, Any]) -> list[dict[str, Any]]:
    """五步行动清单：用已有字段判断做到哪一步，而不是让用户自己打勾。"""
    def filled(*names: str) -> bool:
        return all(str(work.get(name, "")).strip() for name in names)
    return [
        {"step": 1, "label": "第一遍：回答五个 C，写下读 / 存档 / 不读", "done": filled("category", "contribution") and bool(work.get("decision"))},
        {"step": 2, "label": "第二遍：写清方法、实验、结果、局限", "done": filled("method_note", "data_setup", "results")},
        {"step": 3, "label": "第三遍（核心文献）：记录复现细节与弱点", "done": filled("pass3_note")},
        {"step": 4, "label": "六段式大纲成型，能讲 15 分钟", "done": filled("one_liner", "weakness", "relevance")},
        {"step": 5, "label": "准备好 3–5 个可能被问的问题", "done": sum(1 for item in parse_qa(work.get("qa_json")).values() if item["answer"].strip()) >= 3},
    ]


def progress(work: dict[str, Any]) -> int:
    items = checklist(work)
    return int(sum(1 for item in items if item["done"]) / len(items) * 100)


def literature_card(work: dict[str, Any]) -> str:
    """一页纸文献卡：固定字段，方便写综述时横向对比。"""
    lines = [
        f"标题：{work.get('title', '')}",
        f"作者 / 出处：{work.get('authors', '') or '—'} · {work.get('venue', '') or '—'} · {work.get('year', '') or '—'}",
        f"一句话总结：{work.get('one_liner', '') or '—'}",
        f"核心方法：{work.get('method_note', '') or '—'}",
        f"数据与指标：{work.get('data_setup', '') or '—'}",
        f"主要结果：{work.get('results', '') or '—'}",
        f"我认为的不足：{work.get('weakness', '') or '—'}",
        f"与我的课题：{work.get('relevance', '') or '—'}",
    ]
    return "\n".join(lines)


def outline(work: dict[str, Any]) -> list[dict[str, Any]]:
    """六段式汇报大纲。字段缺失时给出该段该写什么，而不是留空。"""
    scenario = SCENARIOS.get(str(work.get("scenario", "group")), SCENARIOS["group"])
    return [
        {
            "title": "1. 封面",
            "lines": [
                work.get("title", "") or "论文标题",
                f"{work.get('authors', '') or '作者'} · {work.get('venue', '') or '期刊/会议'} · {work.get('year', '') or '年份'}",
                "汇报人 / 日期",
            ],
        },
        {
            "title": "2. 背景与动机",
            "lines": [
                work.get("context", "") or "研究对象是什么、为什么重要",
                f"现有方法的缺陷：{work.get('correctness', '') or '写清作者认为的缺口'}",
            ],
        },
        {
            "title": "3. 方法（重点）",
            "lines": [
                work.get("one_liner", "") or "一句话说清它怎么做的",
                work.get("method_note", "") or "自己画一遍方法总览图，讲清每个模块的输入输出",
            ],
        },
        {
            "title": "4. 实验结果",
            "lines": [
                f"数据与设置：{work.get('data_setup', '') or '数据集、baseline、是否公平'}",
                f"主要结果：{work.get('results', '') or '只放最核心的表，配一句结论'}",
            ],
        },
        {
            "title": "5. 结论与不足",
            "lines": [
                f"作者承认的局限：{work.get('weakness', '') or '看结论最后一段的 future work'}",
            ],
        },
        {
            "title": "6. 创新点与借鉴价值",
            "lines": [
                f"可复用到我课题的部分：{work.get('relevance', '') or '从思路、实验手段、结论三处各找一条'}",
                f"场景侧重：{scenario['focus']}",
            ],
        },
    ]


def outline_text(work: dict[str, Any]) -> str:
    blocks = []
    for section in outline(work):
        blocks.append(section["title"])
        for line in section["lines"]:
            if str(line).strip():
                blocks.append(f"  - {line}")
        blocks.append("")
    return "\n".join(blocks).strip()


def qa_questions(work: dict[str, Any]) -> list[dict[str, str]]:
    answers = parse_qa(work.get("qa_json"))
    return [
        {"key": key, "label": label, "question": answers[key]["question"], "answer": answers[key]["answer"]}
        for key, label, _default in QA_TREE
    ]
