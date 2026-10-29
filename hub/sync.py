"""联机同步：把成员节点推送的事件安全地落到中心库。

apply_event 是中心端唯一的"外部写入"入口：一切事件都经过白名单、
上限与幂等校验，杜绝把成员数据直接写进中心表。
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from hub.common import parse_theme
from hub.db import balances, now_iso, transact_asset
from services.game_world import ARTIFACTS, BUILDINGS

ALLOWED_EVENT_TYPES = {
    "initial_state_claim",
    "mission_completed",
    "day_cleared",
    "mission_postponed",
    "profile_updated",
    "personalization_updated",
    "welfare_claimed",
    "artifact_buy",
    "artifact_upgrade",
    "artifact_equip",
    "building_upgrade",
}
MAX_EVENT_PAYLOAD_BYTES = 32 * 1024


def apply_event(conn, user_id: int, event: dict[str, Any]) -> dict[str, Any]:
    event_uuid = str(event.get("event_uuid", ""))[:80]
    event_type = str(event.get("event_type", ""))[:60]
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if not event_uuid or not event_type:
        return {"event_uuid": event_uuid, "status": "rejected", "message": "缺少事件编号或类型"}
    if event_type not in ALLOWED_EVENT_TYPES:
        return {"event_uuid": event_uuid, "status": "rejected", "message": "不支持的事件类型"}
    payload_json = json.dumps(payload, ensure_ascii=False)
    if len(payload_json.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        return {"event_uuid": event_uuid, "status": "rejected", "message": "事件内容过大"}
    existing = conn.execute(
        "SELECT user_id,result_json FROM hub_sync_events WHERE event_uuid=?",
        (event_uuid,),
    ).fetchone()
    if existing:
        if int(existing["user_id"]) != int(user_id):
            return {"event_uuid": event_uuid, "status": "rejected", "message": "事件编号冲突"}
        try:
            result = json.loads(existing["result_json"] or "{}")
        except json.JSONDecodeError:
            result = {}
        return {"event_uuid": event_uuid, "status": "duplicate", **result}

    result: dict[str, Any] = {"message": "已记录", "rewards": {}}
    rewarded = 0
    today = datetime.now().astimezone().date().isoformat()
    if event_type == "initial_state_claim":
        prior = conn.execute(
            "SELECT COUNT(*) n FROM hub_sync_events WHERE user_id=? AND event_type='initial_state_claim' AND rewarded=1",
            (user_id,),
        ).fetchone()["n"]
        if prior:
            result = {"message": "初始状态已经迁移过", "rewards": {}}
        else:
            requested_balances = payload.get("balances") if isinstance(payload.get("balances"), dict) else {}
            limits = {"spirit_stone": 100000, "spirit_wood": 10000, "mystic_iron": 10000, "star_sand": 10000}
            current_balances = balances(conn, user_id)
            for key, upper in limits.items():
                target = max(0, min(int(requested_balances.get(key, current_balances.get(key, 0)) or 0), upper))
                delta = target - int(current_balances.get(key, 0))
                if delta:
                    transact_asset(conn, user_id, key, delta, "首次联机迁移", event_uuid)
            requested_inventory = payload.get("inventory") if isinstance(payload.get("inventory"), list) else []
            allowed_items = set(ARTIFACTS) | set(BUILDINGS)
            for raw in requested_inventory[:100]:
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("item_key", ""))
                if key not in allowed_items:
                    continue
                item_type = "artifact" if key in ARTIFACTS else "building"
                conn.execute(
                    """INSERT INTO hub_inventory(user_id,item_key,item_type,quantity,level,equipped,updated_at)
                       VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id,item_key) DO UPDATE SET
                       quantity=MAX(hub_inventory.quantity,excluded.quantity),
                       level=MAX(hub_inventory.level,excluded.level),
                       equipped=excluded.equipped,updated_at=excluded.updated_at""",
                    (user_id,key,item_type,max(1,min(int(raw.get("quantity",1) or 1),99)),max(1,min(int(raw.get("level",1) or 1),99)),int(bool(raw.get("equipped",0))),now_iso()),
                )
            result = {"message": "本地初始资产已迁移", "rewards": {}}
            rewarded = 1
    elif event_type == "mission_completed":
        rewarded_today = conn.execute(
            """SELECT COUNT(*) n FROM hub_sync_events
               WHERE user_id=? AND event_type='mission_completed' AND rewarded=1 AND substr(created_at,1,10)=?""",
            (user_id, today),
        ).fetchone()["n"]
        if rewarded_today < 4:
            requested = payload.get("rewards") if isinstance(payload.get("rewards"), dict) else {}
            stone_amount = max(1, min(int(requested.get("spirit_stone", 8) or 8), 40))
            reward_asset = next((key for key in ("spirit_wood", "mystic_iron", "star_sand") if int(requested.get(key, 0) or 0) > 0), "spirit_wood")
            material_amount = max(1, min(int(requested.get(reward_asset, 1) or 1), 3))
            transact_asset(conn, user_id, "spirit_stone", stone_amount, "联机任务交付", event_uuid)
            transact_asset(conn, user_id, reward_asset, material_amount, "联机任务交付", event_uuid)
            result = {"message": "交付已同步", "rewards": {"spirit_stone": stone_amount, reward_asset: material_amount}}
            rewarded = 1
        else:
            result = {"message": "已记录；今日联机奖励达到4次上限", "rewards": {}}
    elif event_type == "day_cleared":
        transact_asset(conn, user_id, "spirit_stone", 5, "联机通关奖励", event_uuid)
        result = {"message": "通关已同步", "rewards": {"spirit_stone": 5}}
        rewarded = 1
    elif event_type == "mission_postponed":
        try:
            transact_asset(conn, user_id, "spirit_stone", -2, "联机任务推迟", event_uuid)
            result = {"message": "推迟已同步", "rewards": {"spirit_stone": -2}}
            rewarded = 1
        except ValueError:
            result = {"message": "联机灵石不足，中心未扣款", "rewards": {}}
    elif event_type == "profile_updated":
        display_name = str(payload.get("display_name", ""))[:40].strip()
        if display_name:
            conn.execute("UPDATE accounts SET display_name=?,updated_at=? WHERE id=?", (display_name, now_iso(), user_id))
        conn.execute(
            """UPDATE account_profiles SET title=?,bio=?,skills=?,capabilities=?,goals=?,avatar_symbol=?,revision=revision+1,updated_at=? WHERE account_id=?""",
            (
                str(payload.get("title", ""))[:100], str(payload.get("bio", ""))[:1000],
                str(payload.get("skills", ""))[:2000], str(payload.get("capabilities", ""))[:3000],
                str(payload.get("goals", ""))[:1500], str(payload.get("avatar_symbol", "道"))[:2] or "道",
                now_iso(), user_id,
            ),
        )
        result = {"message": "个人信息已同步", "rewards": {}}
    elif event_type == "personalization_updated":
        theme = parse_theme(payload)
        conn.execute("UPDATE account_profiles SET theme_json=?,revision=revision+1,updated_at=? WHERE account_id=?", (json.dumps(theme, ensure_ascii=False), now_iso(), user_id))
        result = {"message": "个性化配置已同步", "rewards": {}}
    elif event_type == "welfare_claimed":
        try:
            grant_id = int(payload.get("grant_id", 0))
        except (TypeError, ValueError):
            grant_id = 0
        row = conn.execute(
            "SELECT id,claimed_at FROM hub_welfare_grants WHERE id=? AND recipient_id=?",
            (grant_id, user_id),
        ).fetchone()
        if not row:
            result = {"message": "福利记录不存在或不属于当前成员", "rewards": {}}
        elif row["claimed_at"]:
            result = {"message": "福利已确认", "rewards": {}}
        else:
            conn.execute("UPDATE hub_welfare_grants SET claimed_at=? WHERE id=?", (now_iso(), grant_id))
            result = {"message": "福利已确认", "rewards": {}}
    elif event_type in {"artifact_buy", "artifact_upgrade", "artifact_equip", "building_upgrade"}:
        item_key = str(payload.get("item_key", ""))
        if event_type.startswith("artifact"):
            spec = ARTIFACTS.get(item_key)
            item_type = "artifact"
        else:
            spec = BUILDINGS.get(item_key)
            item_type = "building"
        if not spec:
            result = {"message": "未知物品，未同步", "rewards": {}}
        else:
            row = conn.execute("SELECT * FROM hub_inventory WHERE user_id=? AND item_key=?", (user_id, item_key)).fetchone()
            if event_type == "artifact_buy":
                if row:
                    result = {"message": "已拥有该法器", "rewards": {}}
                else:
                    try:
                        transact_asset(conn, user_id, "spirit_stone", -int(spec["price"]), f"购入{spec['name']}", event_uuid)
                        conn.execute("INSERT INTO hub_inventory(user_id,item_key,item_type,quantity,level,equipped,updated_at) VALUES (?,?,?,1,1,0,?)", (user_id,item_key,item_type,now_iso()))
                        result = {"message": "法器已同步", "rewards": {"spirit_stone": -int(spec["price"])}}
                        rewarded = 1
                    except ValueError:
                        result = {"message": "联机灵石不足，购买未同步", "rewards": {}}
            elif event_type == "artifact_upgrade":
                if not row:
                    result = {"message": "中心尚无该法器", "rewards": {}}
                else:
                    level = int(row["level"])
                    cost = max(10, (int(spec["price"]) // 2) * max(level, 1))
                    try:
                        transact_asset(conn, user_id, "spirit_stone", -cost, f"淬炼{spec['name']}", event_uuid)
                        conn.execute("UPDATE hub_inventory SET level=level+1,updated_at=? WHERE user_id=? AND item_key=?", (now_iso(),user_id,item_key))
                        result = {"message": "法器等级已同步", "rewards": {"spirit_stone": -cost}}
                        rewarded = 1
                    except ValueError:
                        result = {"message": "联机灵石不足，淬炼未同步", "rewards": {}}
            elif event_type == "artifact_equip":
                if row:
                    conn.execute("UPDATE hub_inventory SET equipped=0 WHERE user_id=? AND item_type='artifact'", (user_id,))
                    conn.execute("UPDATE hub_inventory SET equipped=1,updated_at=? WHERE user_id=? AND item_key=?", (now_iso(),user_id,item_key))
                    result = {"message": "佩戴状态已同步", "rewards": {}}
                else:
                    result = {"message": "中心尚无该法器", "rewards": {}}
            else:
                level = int(row["level"]) if row else 0
                asset_key = spec["asset"]
                cost = int(spec["base_cost"] * max(1, level + 1))
                try:
                    transact_asset(conn, user_id, asset_key, -cost, f"升级{spec['name']}", event_uuid)
                    conn.execute(
                        """INSERT INTO hub_inventory(user_id,item_key,item_type,quantity,level,equipped,updated_at)
                           VALUES (?,?,?,1,1,0,?) ON CONFLICT(user_id,item_key) DO UPDATE SET level=hub_inventory.level+1,updated_at=excluded.updated_at""",
                        (user_id,item_key,item_type,now_iso()),
                    )
                    result = {"message": "建筑等级已同步", "rewards": {asset_key: -cost}}
                    rewarded = 1
                except ValueError:
                    result = {"message": "联机资材不足，升级未同步", "rewards": {}}
    conn.execute(
        "INSERT INTO hub_sync_events(user_id,event_uuid,event_type,payload_json,rewarded,result_json,created_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, event_uuid, event_type, payload_json, rewarded, json.dumps(result, ensure_ascii=False), now_iso()),
    )
    return {"event_uuid": event_uuid, "status": "applied", **result}



