from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RealmStage:
    key: str
    threshold: int
    required_xp: int
    name: str
    description: str
    major: str
    phase: str


@dataclass(frozen=True)
class TribulationGate:
    key: str
    from_key: str
    to_key: str
    title: str


# Each entry is (stable key, XP required from the previous stage, display name,
# research-growth description, major realm, phase).  Requirements increase
# monotonically and are deliberately all different.  Keeping requirements
# explicit makes the curve inspectable instead of hiding it in a formula.
_REALM_BLUEPRINTS: tuple[tuple[str, int, str, str, str, str], ...] = (
    ("mortal", 0, "凡人", "开始留下第一份可验证的科研痕迹", "mortal", "single"),
    ("body_early", 40, "锻体前期", "建立作息、文件与最小交付习惯", "body", "early"),
    ("body_middle", 55, "锻体中期", "能够连续完成短时学习行动", "body", "middle"),
    ("body_late", 75, "锻体后期", "开始把输入转成笔记、图表或代码", "body", "late"),
    ("qi_early", 100, "练气前期", "掌握本领域最基础的概念、单位与术语", "qi", "early"),
    ("qi_middle", 130, "练气中期", "能复述关键机制并指出适用边界", "qi", "middle"),
    ("qi_late", 170, "练气后期", "能读懂常见图表并独立完成基础案例", "qi", "late"),
    ("foundation_early", 220, "筑基前期", "建立自己的知识库与记录规范", "foundation", "early"),
    ("foundation_middle", 280, "筑基中期", "能从文献中提取可验证的证据", "foundation", "middle"),
    ("foundation_late", 350, "筑基后期", "能把问题拆成变量、机制与观测量", "foundation", "late"),
    ("core_early", 440, "金丹前期", "拥有首批可复用的数据、SOP与案例", "core", "early"),
    ("core_middle", 540, "金丹中期", "能设计对照并识别主要限制步骤", "core", "middle"),
    ("core_late", 660, "金丹后期", "形成一条证据闭环的研究叙事", "core", "late"),
    ("nascent_early", 800, "元婴前期", "能独立推进一个边界清晰的问题", "nascent", "early"),
    ("nascent_middle", 960, "元婴中期", "能协调实验、模拟或数据分析支线", "nascent", "middle"),
    ("nascent_late", 1140, "元婴后期", "能判断哪些结果值得继续投入", "nascent", "late"),
    ("spirit_early", 1340, "化神前期", "能把复杂机制讲清并接受证据检验", "spirit", "early"),
    ("spirit_middle", 1560, "化神中期", "能将知识资产转化为论文图与方法", "spirit", "middle"),
    ("spirit_late", 1800, "化神后期", "能提出具有区分度的科学问题", "spirit", "late"),
    ("void_early", 2060, "练虚前期", "能把多尺度证据放入同一解释框架", "void", "early"),
    ("void_middle", 2340, "练虚中期", "能建立可复现、可审计的研究流程", "void", "middle"),
    ("void_late", 2640, "练虚后期", "能够搭建团队可复用的科研基础设施", "void", "late"),
    ("fusion_early", 2960, "合体前期", "知识、数据和方法开始协同", "fusion", "early"),
    ("fusion_middle", 3300, "合体中期", "实验、模拟与表达形成闭环", "fusion", "middle"),
    ("fusion_late", 3660, "合体后期", "能稳定协调复杂项目与协作者", "fusion", "late"),
    ("mahayana_early", 4040, "大乘前期", "能够定义重要且可回答的问题", "mahayana", "early"),
    ("mahayana_middle", 4440, "大乘中期", "能够组织高质量证据并形成体系", "mahayana", "middle"),
    ("mahayana_late", 4860, "大乘后期", "能够引导一条研究方向持续演进", "mahayana", "late"),
    ("tribulation_early", 5300, "渡劫前期", "用连续挑战检查知识体系的薄弱处", "tribulation", "early"),
    ("tribulation_middle", 5760, "渡劫中期", "能在压力与不确定性下保持严谨判断", "tribulation", "middle"),
    ("tribulation_late", 6240, "渡劫后期", "完成从个人能力到可传承体系的跃迁", "tribulation", "late"),
    ("human_immortal", 6740, "人仙", "形成稳定、独立且可持续的科研创造能力", "human_immortal", "single"),
    ("earth_immortal", 7260, "地仙", "能够深耕一域并持续产出可信成果", "earth_immortal", "single"),
    ("heaven_immortal", 7800, "天仙", "能够跨越领域边界组织新问题", "heaven_immortal", "single"),
    ("mystic_immortal", 8360, "玄仙", "能够从复杂现象中提炼普适机制", "mystic_immortal", "single"),
    ("golden_immortal", 8940, "金仙", "拥有经得起长期检验的方法与判断体系", "golden_immortal", "single"),
    ("great_luo", 9540, "大罗金仙", "能够建立有持续影响力的研究范式", "great_luo", "single"),
    ("quasi_sage", 10160, "准圣", "能够培养他人并推动共同知识边界", "quasi_sage", "single"),
    ("sage", 10800, "圣人", "形成可创造、验证、传承并造福他人的科研体系", "sage", "single"),
)


def _build_realm_stages() -> tuple[RealmStage, ...]:
    threshold = 0
    stages: list[RealmStage] = []
    for key, required_xp, name, description, major, phase in _REALM_BLUEPRINTS:
        threshold += required_xp
        stages.append(
            RealmStage(
                key=key,
                threshold=threshold,
                required_xp=required_xp,
                name=name,
                description=description,
                major=major,
                phase=phase,
            )
        )
    return tuple(stages)


REALM_STAGES: tuple[RealmStage, ...] = _build_realm_stages()
REALM_BY_KEY = {stage.key: stage for stage in REALM_STAGES}
REALM_INDEX = {stage.key: index for index, stage in enumerate(REALM_STAGES)}


# A thunder tribulation is a major-realm gate, not a repeatable review button.
# The first one appears only after the user is already in Golden Core and has
# accumulated enough XP to enter Nascent Soul.
_GATE_TARGETS: tuple[tuple[str, str], ...] = (
    ("core_late", "nascent_early"),
    ("nascent_late", "spirit_early"),
    ("spirit_late", "void_early"),
    ("void_late", "fusion_early"),
    ("fusion_late", "mahayana_early"),
    ("mahayana_late", "tribulation_early"),
    ("tribulation_late", "human_immortal"),
    ("human_immortal", "earth_immortal"),
    ("earth_immortal", "heaven_immortal"),
    ("heaven_immortal", "mystic_immortal"),
    ("mystic_immortal", "golden_immortal"),
    ("golden_immortal", "great_luo"),
    ("great_luo", "quasi_sage"),
    ("quasi_sage", "sage"),
)
TRIBULATION_GATES: tuple[TribulationGate, ...] = tuple(
    TribulationGate(
        key=f"{from_key}__{to_key}",
        from_key=from_key,
        to_key=to_key,
        title=f"{REALM_BY_KEY[from_key].name} → {REALM_BY_KEY[to_key].name}",
    )
    for from_key, to_key in _GATE_TARGETS
)
TRIBULATION_GATE_BY_KEY = {gate.key: gate for gate in TRIBULATION_GATES}


# v1.4 used 31 different keys and names.  Defaults are recognized so an
# untouched old installation adopts the new requested names, while genuinely
# customized labels remain attached to the closest equivalent stage.
LEGACY_V4_DEFAULT_LABELS = {
    "mortal": "凡人",
    "body_early": "炼体初境",
    "body_middle": "炼体中境",
    "body_late": "炼体后境",
    "body_complete": "炼体圆满",
    "qi_1": "炼气一层",
    "qi_3": "炼气三层",
    "qi_6": "炼气六层",
    "qi_9": "炼气九层",
    "qi_complete": "炼气圆满",
    "foundation_early": "筑基初期",
    "foundation_middle": "筑基中期",
    "foundation_late": "筑基后期",
    "foundation_complete": "筑基圆满",
    "core_early": "金丹初期",
    "core_middle": "金丹中期",
    "core_late": "金丹后期",
    "core_complete": "金丹圆满",
    "nascent_early": "元婴初期",
    "nascent_middle": "元婴中期",
    "nascent_late": "元婴后期",
    "nascent_complete": "元婴圆满",
    "spirit_early": "化神初期",
    "spirit_middle": "化神中期",
    "spirit_late": "化神后期",
    "spirit_complete": "化神圆满",
    "void": "炼虚境",
    "fusion": "合体境",
    "mahayana": "大乘境",
    "tribulation": "渡劫境",
    "true_immortal": "真仙境",
}
LEGACY_V4_ALIASES = {
    "body_complete": "body_late",
    "qi_1": "qi_early",
    "qi_3": "qi_middle",
    "qi_6": "qi_late",
    "qi_9": "qi_late",
    "qi_complete": "qi_late",
    "foundation_complete": "foundation_late",
    "core_complete": "core_late",
    "nascent_complete": "nascent_late",
    "spirit_complete": "spirit_late",
    "void": "void_early",
    "fusion": "fusion_early",
    "mahayana": "mahayana_early",
    "tribulation": "tribulation_early",
    "true_immortal": "human_immortal",
}
LEGACY_V1_TARGET_KEYS: tuple[str, ...] = (
    "mortal",
    "qi_early",
    "qi_middle",
    "qi_late",
    "foundation_early",
    "foundation_late",
    "core_early",
    "core_late",
    "nascent_late",
    "spirit_late",
    "void_early",
    "fusion_early",
    "mahayana_early",
    "tribulation_early",
)


def default_realm_labels() -> dict[str, str]:
    return {stage.key: stage.name for stage in REALM_STAGES}


def normalize_realm_labels(value: Any) -> dict[str, str]:
    labels = default_realm_labels()
    if isinstance(value, dict):
        applied: set[str] = set()
        for stage in REALM_STAGES:
            candidate = str(value.get(stage.key, "")).strip()[:30]
            if not candidate:
                continue
            if LEGACY_V4_DEFAULT_LABELS.get(stage.key) == candidate:
                continue
            labels[stage.key] = candidate
            applied.add(stage.key)
        for old_key, target_key in LEGACY_V4_ALIASES.items():
            if target_key in applied:
                continue
            candidate = str(value.get(old_key, "")).strip()[:30]
            if not candidate or LEGACY_V4_DEFAULT_LABELS.get(old_key) == candidate:
                continue
            labels[target_key] = candidate
        return labels
    if isinstance(value, list):
        keys = (
            LEGACY_V1_TARGET_KEYS
            if len(value) == len(LEGACY_V1_TARGET_KEYS)
            else tuple(stage.key for stage in REALM_STAGES)
        )
        for index, candidate in enumerate(value):
            if index >= len(keys):
                break
            text = str(candidate).strip()[:30]
            if text:
                labels[keys[index]] = text
    return labels


def first_pending_tribulation(
    xp: int,
    passed_gate_keys: Iterable[str] = (),
) -> TribulationGate | None:
    passed = set(passed_gate_keys)
    for gate in TRIBULATION_GATES:
        if xp >= REALM_BY_KEY[gate.to_key].threshold and gate.key not in passed:
            return gate
    return None


def realm_state(
    xp: int,
    labels: dict[str, str] | None = None,
    passed_gate_keys: Iterable[str] = (),
) -> dict[str, Any]:
    xp = max(0, int(xp or 0))
    names = normalize_realm_labels(labels or {})
    natural_index = 0
    for index, stage in enumerate(REALM_STAGES):
        if xp >= stage.threshold:
            natural_index = index
        else:
            break

    pending_gate = first_pending_tribulation(xp, passed_gate_keys)
    current_index = (
        REALM_INDEX[pending_gate.from_key]
        if pending_gate is not None
        else natural_index
    )
    stage = REALM_STAGES[current_index]

    if pending_gate is not None:
        next_stage = REALM_BY_KEY[pending_gate.to_key]
        progress = 100
        remaining = 0
    elif current_index + 1 < len(REALM_STAGES):
        next_stage = REALM_STAGES[current_index + 1]
        progress = int(
            (xp - stage.threshold)
            / max(next_stage.threshold - stage.threshold, 1)
            * 100
        )
        remaining = max(next_stage.threshold - xp, 0)
    else:
        next_stage = stage
        progress = 100
        remaining = 0

    return {
        "key": stage.key,
        "name": names[stage.key],
        "description": stage.description,
        "threshold": stage.threshold,
        "required_xp": stage.required_xp,
        "next_threshold": next_stage.threshold,
        "next_required_xp": next_stage.required_xp,
        "next_name": (
            names[next_stage.key]
            if next_stage.key != stage.key
            else "已至圣人"
        ),
        "next_key": next_stage.key,
        "progress": max(0, min(progress, 100)),
        "remaining": remaining,
        "tribulation_required": pending_gate is not None,
        "tribulation_gate": pending_gate.key if pending_gate else "",
        "tribulation_title": (
            f"{names[pending_gate.from_key]} → {names[pending_gate.to_key]}"
            if pending_gate
            else ""
        ),
    }


DAILY_XP_BANDS: tuple[tuple[int, int, str], ...] = (
    (20, 6, "微行动"),
    (45, 10, "标准行动"),
    (75, 16, "专注行动"),
    (120, 22, "深度行动"),
    (10_000, 30, "长时行动"),
)


def fixed_daily_xp(duration_minutes: int) -> int:
    minutes = max(5, min(int(duration_minutes or 30), 240))
    for upper, xp, _ in DAILY_XP_BANDS:
        if minutes <= upper:
            return xp
    return DAILY_XP_BANDS[-1][1]


CULTIVATION_TASK_XP = {1: 25, 2: 50, 3: 90}
CULTIVATION_DIFFICULTY_LABELS = {1: "小成", 2: "进阶", 3: "突破"}


def fixed_cultivation_xp(difficulty: int) -> int:
    return CULTIVATION_TASK_XP.get(
        max(1, min(int(difficulty or 1), 3)),
        25,
    )
