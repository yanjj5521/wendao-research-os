from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from core.navigation import DEFAULT_NAV_LABELS, DEFAULT_NAV_LAYOUT
from services.progression import default_realm_labels, fixed_cultivation_xp
from core.version import APP_VERSION
from services.workspace_profiles import DEFAULT_WORKSPACES, profile_for


def seed_clean_database(conn: sqlite3.Connection, now_iso: Callable[[], str]) -> None:
    defaults = {
        "site_name": "问道科研",
        "researcher_name": "修士",
        "domains": json.dumps(
            [
                "电化学", "水泥基能源材料", "膨胀石墨", "分子动力学", "机器学习",
                "英语与雅思", "科研写作", "实验方法", "数据与编程", "未分类",
            ],
            ensure_ascii=False,
        ),
        "ai_mode": "offline",
        "ai_endpoint": "http://127.0.0.1:11434/api/generate",
        "ai_model": "qwen2.5:7b",
        "portable_version": APP_VERSION,
        "foundation_master_text": "",
        "hub_url": "",
        "hub_api_token": "",
        "hub_auto_sync": "0",
        "sync_provider": "disabled",
        "sync_contract_version": "2026-07-27",
        "ui_accent": "terracotta",
        "ui_density": "comfortable",
        "home_layout": json.dumps(
            [
                {"key": "gate", "span": 12},
                {"key": "search", "span": 12},
                {"key": "shortcuts", "span": 12},
                {"key": "workbench", "span": 12},
                {"key": "continuity", "span": 12},
            ],
            ensure_ascii=False,
        ),
        "ui_scene": "warm",
        "ui_motion": "balanced",
        "ui_geometry": "soft",
        "ui_font_scale": "normal",
        "ui_home_effect": "orbits",
        "ui_home_motto": "把学习变成可以积累的事",
        "ui_home_poem": "纸上得来终觉浅，绝知此事要躬行。——陆游",
        "ui_poem_pool": "[]",
        "avatar_file": "",
        "review_popup": "1",
        "gamification": "on",
        "career_phase": "foundation",
        "career_focus": "把每天的学习变成能带走的能力，而不是一次性看完的内容。",
        "career_boundary": "不要求把每个方向都学完；只保证当前这一层能复述、能用、能迁移。",
        "career_success_signal": "能不看资料讲清一个概念，并在新问题上用出来。",
        "career_review_date": "",
        "realm_names": json.dumps(default_realm_labels(), ensure_ascii=False),
        "nav_labels": json.dumps(DEFAULT_NAV_LABELS, ensure_ascii=False),
        "nav_layout": json.dumps(DEFAULT_NAV_LAYOUT, ensure_ascii=False),
    }
    conn.executemany(
        "INSERT INTO settings(key,value) VALUES (?,?)",
        defaults.items(),
    )

    ts = now_iso()
    workspace_ids: dict[str, int] = {}
    # The homepage dock holds at most six domains, so only the first six
    # defaults are pinned; the rest stay available in the sidebar.
    for index, workspace in enumerate(DEFAULT_WORKSPACES):
        profile = profile_for(workspace["module"])
        cursor = conn.execute(
            """
            INSERT INTO workspaces(
                workspace_key,name,icon,module,description,accent,sort_order,
                active,pinned_home,objective,workflow_json,toolset_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?)
            """,
            (
                workspace["workspace_key"],
                workspace["name"],
                workspace["icon"],
                workspace["module"],
                workspace["description"],
                workspace["accent"],
                workspace["sort_order"],
                1 if index < 6 else 0,
                profile["objective"],
                json.dumps(profile["workflow"], ensure_ascii=False),
                json.dumps(profile["tools"], ensure_ascii=False),
                ts,
                ts,
            ),
        )
        workspace_ids[workspace["workspace_key"]] = int(cursor.lastrowid)

    conn.execute(
        """
        INSERT INTO player_profile(
            id,display_name,title,bio,skills,capabilities,goals,avatar_symbol,updated_at
        ) VALUES (1,?,?,?,?,?,?,?,?)
        """,
        (
            "修士",
            "长期学习者 · 材料与能源",
            "从博士入学开始，用每日学习、真实交付和持续复盘，建立一套能一直用到毕业之后的学习系统。",
            "电化学基础\n水泥基材料\n膨胀石墨实验\nLAMMPS入门\nPython数据处理",
            "能复现基础案例\n能建立实验台账\n能拆解论文图表\n能用GPT辅助学习与排错",
            "让每一次学习、练习、试错和输出都沉淀为下一阶段可调用的能力",
            "道",
            ts,
        ),
    )

    conn.executemany(
        "INSERT INTO asset_transactions(asset_key,amount,reason,created_at) VALUES (?,?,?,?)",
        [
            ("spirit_stone", 12, "正式版开宗礼包", ts),
            ("spirit_wood", 4, "正式版开宗礼包", ts),
            ("mystic_iron", 2, "正式版开宗礼包", ts),
            ("star_sand", 2, "正式版开宗礼包", ts),
        ],
    )

    starter_eggs = [
        ("moon_well", "月影井", "你在洞府角落找到了一口井。科研中的空白，有时比答案更值得凝视。"),
        ("first_delivery", "第一枚玉简", "你第一次用真实交付证明：今天不是只看懂了，而是留下了可复用的痕迹。"),
        ("seven_deliveries", "七日炼心", "七次交付后，你开始从‘知道’走向‘能够稳定做到’。"),
        ("image_note", "画中有道", "一张图进入笔记，文字与视觉开始共同承担思考。"),
        ("all_herbs", "百草同春", "所有方向都萌芽了。广度不是分散，而是让不同能力开始互相供养。"),
        ("balanced_plan", "七日有度", "一份近期计划既照顾眼前卡点，也为每天留下了可完成的交付。"),
        ("many_workspaces", "诸域同参", "一个课题第一次同时连接多个工作区，实验、计算与写作开始共享同一问题。"),
        ("ai_handoff", "借智留痕", "你没有让 AI 对话随窗口消失，而是把结论、证据与下一步重新收进系统。"),
        ("evidence_gate", "一证破关", "第一个证据闸门不是靠感觉，而是凭证据与决策理由通过。"),
        ("failure_alchemy", "败中炼金", "一次失败被写成可复用的预防规则，损失开始转化为方法。"),
        ("constellation", "几何星图", "你在山门的几何轨道里找到了隐藏节点。连接本身，也是一种发现。"),
        ("artifact_keeper", "百器归心", "你收集了五件法器，也开始理解工具只有在真实工作中生效才有意义。"),
        ("trial_triad", "三境同游", "你完成了三种不同目的的秘境：回忆、迁移与反证开始互相支撑。"),
        ("review_scribe", "十简成卷", "十次交付留下了可复盘关键文本，个人题库开始拥有连续性。"),
        ("career_witness", "回望有迹", "生涯罗盘记录了第一个重要节点，未来的你能够看见为何转向。"),
        ("home_architect", "山门营造师", "你亲手调整了山门布局。工具开始适应你，而不是让你适应工具。"),
        ("auto_harmony", "一键归序", "凌乱的模块重新各归其位。秩序不是束缚，而是减少不必要的选择。"),
        ("first_pill", "炉火初红", "第一枚丹药出炉；真正珍贵的不是丹药，而是它背后的真实交付。"),
        ("five_recipes", "五方丹成", "五种丹方都曾被炼成，行动、复盘与方法开始互相供养。"),
        ("tribulation_survivor", "雷后见山", "一次五问雷劫已经通过。突破来自能经得住追问的证据。"),
        ("hundred_entries", "百简归阁", "知识库收下第一百条记录，零散积累开始显出结构。"),
        ("deep_focus", "一刻无尘", "在静室完成一次完整专注，注意力被重新交还给真正重要的问题。"),
        ("night_scholar", "星夜未央", "在星夜场景中留下了一次真实交付。夜色温柔，证据仍需清醒。"),
        ("tag_weaver", "标签织网", "五十条资料获得标签，检索的道路开始从记忆变成结构。"),
        ("data_guardian", "备份守门人", "你完成了一次完整备份。可恢复，才是真正拥有。"),
        ("member_handshake", "同道相逢", "第一次与同行会完成同步。协作从明确边界开始。"),
        ("resource_beacon", "驿站灯火", "第一张资料卡被分享，而原始资料仍安全留在本地。"),
        ("realm_cartographer", "境界绘图者", "你调整了自己的境界名称，让成长语言更贴近真实道路。"),
        ("poem_keeper", "诗藏四时", "山门拥有了自定义诗句池，每一天都留下一句不同的回声。"),
        ("quiet_master", "无声胜有声", "你关闭了主页动效，却没有关闭前进。克制也是一种设计。"),
    ]
    conn.executemany(
        "INSERT INTO easter_eggs(egg_key,title,description) VALUES (?,?,?)",
        starter_eggs,
    )

    starter_quests = [
        ("建立第一张论文证据卡", "上传一篇与你当前问题直接相关的论文，并写清它证明了什么。", "论文条目 + 证据摘要", 1, None),
        ("形成第一个可证伪问题", "把模糊想法改写为变量、机制、结果和判断边界。", "问题—假设—证据—下一步卡片", 1, None),
        ("建立第一个数据集档案", "上传 CSV 或 XLSX，并补充变量、单位和来源说明。", "可检索数据集 + 数据字典", 2, "dataset-lab"),
        ("完成一次失败复盘", "记录一次实验或代码失败，并明确下次如何更早发现。", "失败现象—根因—修正—预防记录", 2, None),
        ("建立第一个实验批次", "把条件、观测指标、异常和结论录入通用实验台账。", "一个字段完整的实验批次", 2, "eg-lab"),
        ("完成一次英文精读并复述", "选一段论文正文，拆句、查词、讲清论证顺序，再用三句话复述。", "精读笔记 + 三句话复述", 2, "english-lab"),
    ]
    for title, description, deliverable, difficulty, workspace_key in starter_quests:
        conn.execute(
            """
            INSERT INTO quests(
                title,description,deliverable,difficulty,xp,status,workspace_id,created_at,updated_at
            ) VALUES (?,?,?,?,?,'planned',?,?,?)
            """,
            (
                title,
                description,
                deliverable,
                difficulty,
                fixed_cultivation_xp(difficulty),
                workspace_ids.get(workspace_key) if workspace_key else None,
                ts,
                ts,
            ),
        )
