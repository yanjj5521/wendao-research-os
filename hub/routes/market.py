"""同行坊市：红包与资产交换。

规则（与 `hub/social.py` 的限额一致）：
- 红包在发出时立即入账对方，不可撤回；
- 交换在创建时先托管发起方的 offer，成交时同时完成两侧入账，
  取消时原额退回——任何一侧余额不足都会在事务内失败，不会出现负资产。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hub.common import audit, flash, hub_context, hub_url_for, require_user, templates, verify_csrf
from hub.social import (
    SOCIAL_ASSET_LABELS,
    social_amount,
    social_asset,
    social_members,
    social_text,
)
from hub.db import balances, connect_hub, hub_transaction, now_iso, transact_asset

router = APIRouter()

_TRANSFER_QUERY = """
SELECT t.*, s.display_name AS sender_name, r.display_name AS recipient_name
FROM hub_social_transfers t
JOIN accounts s ON s.id = t.sender_id
JOIN accounts r ON r.id = t.recipient_id
WHERE t.sender_id = ? OR t.recipient_id = ?
ORDER BY t.id DESC
LIMIT 40
"""


def _recipient(conn, recipient_id: int, sender_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id,display_name FROM accounts WHERE id=? AND status='active'", (int(recipient_id),)
    ).fetchone()
    if not row or int(recipient_id) == int(sender_id):
        raise ValueError("请选择一位其他在册成员。")
    return dict(row)


def _open_transfer(conn, transfer_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT t.*, s.display_name AS sender_name, r.display_name AS recipient_name
        FROM hub_social_transfers t
        JOIN accounts s ON s.id = t.sender_id
        JOIN accounts r ON r.id = t.recipient_id
        WHERE t.id = ?
        """,
        (int(transfer_id),),
    ).fetchone()
    if not row:
        raise ValueError("这笔红包或交换不存在。")
    if row["status"] != "open":
        raise ValueError("这笔记录已经完成或取消。")
    return dict(row)


@router.get("/market", response_class=HTMLResponse, name="hub_market")
def market_page(request: Request):
    user = require_user(request)
    with connect_hub() as conn:
        market_balances = balances(conn, int(user["id"]))
        recipients = social_members(conn, int(user["id"]))
        transfers = [
            dict(row)
            for row in conn.execute(_TRANSFER_QUERY, (int(user["id"]), int(user["id"])))
        ]
    return templates.TemplateResponse(
        request=request,
        name="market.html",
        context=hub_context(
            request,
            "market",
            social_assets=SOCIAL_ASSET_LABELS,
            market_balances=market_balances,
            recipients=recipients,
            transfers=transfers,
        ),
    )


@router.post("/market/red-packets", name="hub_red_packet_send")
def red_packet_send(
    request: Request,
    recipient_id: int = Form(...),
    asset_key: str = Form(...),
    amount: int = Form(...),
    note: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        with hub_transaction() as conn:
            recipient = _recipient(conn, recipient_id, int(user["id"]))
            asset = social_asset(asset_key)
            amount = social_amount(asset, amount)
            note = social_text(note, limit=160, label="祝福")
            ts = now_iso()
            transact_asset(conn, int(user["id"]), asset, -amount, f"红包给 {recipient['display_name']}")
            transact_asset(conn, int(recipient["id"]), asset, amount, f"来自 {user['display_name']} 的红包")
            conn.execute(
                """
                INSERT INTO hub_social_transfers(
                    sender_id,recipient_id,kind,offer_asset,offer_amount,note,status,created_at
                ) VALUES (?,?, 'red_packet', ?, ?, ?, 'completed', ?)
                """,
                (int(user["id"]), int(recipient["id"]), asset, amount, note, ts),
            )
            audit(conn, int(user["id"]), "red_packet", f"{SOCIAL_ASSET_LABELS[asset]}×{amount} → {recipient['display_name']}", request)
        flash(request, "红包已转入对方账户。")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(hub_url_for(request, "hub_market"), status_code=303)


@router.post("/market/trades", name="hub_trade_create")
def trade_create(
    request: Request,
    recipient_id: int = Form(...),
    offer_asset_key: str = Form(..., alias="offer_asset_key"),
    offer_amount: int = Form(...),
    request_asset_key: str = Form(..., alias="request_asset_key"),
    request_amount: int = Form(...),
    note: str = Form(""),
    csrf_value: str = Form(..., alias="_csrf"),
):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        with hub_transaction() as conn:
            recipient = _recipient(conn, recipient_id, int(user["id"]))
            offer_asset = social_asset(offer_asset_key)
            offer_amount = social_amount(offer_asset, offer_amount)
            request_asset = social_asset(request_asset_key)
            request_amount = social_amount(request_asset, request_amount)
            note = social_text(note, limit=160, label="留言") if note.strip() else ""
            # 托管：先扣发起方的 offer，成交才入账对方；取消原额退回
            transact_asset(conn, int(user["id"]), offer_asset, -offer_amount, f"交换托管（给 {recipient['display_name']}）")
            conn.execute(
                """
                INSERT INTO hub_social_transfers(
                    sender_id,recipient_id,kind,offer_asset,offer_amount,
                    request_asset,request_amount,note,status,created_at
                ) VALUES (?,?, 'trade', ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    int(user["id"]), int(recipient["id"]), offer_asset, offer_amount,
                    request_asset, request_amount, note, now_iso(),
                ),
            )
            audit(conn, int(user["id"]), "trade_create", f"{SOCIAL_ASSET_LABELS[offer_asset]}×{offer_amount} ↔ {SOCIAL_ASSET_LABELS[request_asset]}×{request_amount}", request)
        flash(request, "交换提议已发送，对方接受时才会成交。")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(hub_url_for(request, "hub_market"), status_code=303)


@router.post("/market/trades/{transfer_id}/accept", name="hub_trade_accept")
def trade_accept(request: Request, transfer_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        with hub_transaction() as conn:
            transfer = _open_transfer(conn, transfer_id)
            if int(transfer["recipient_id"]) != int(user["id"]):
                raise ValueError("只有接收方可以接受这次交换。")
            # 对方先拿出 offer（创建时已托管），自己付出 request
            transact_asset(conn, int(user["id"]), transfer["offer_asset"], int(transfer["offer_amount"]), f"交换所得（来自 {transfer['sender_name']}）")
            transact_asset(conn, int(user["id"]), transfer["request_asset"], -int(transfer["request_amount"]), f"交换支付（给 {transfer['sender_name']}）")
            transact_asset(conn, int(transfer["sender_id"]), transfer["request_asset"], int(transfer["request_amount"]), f"交换所得（来自 {user['display_name']}）")
            conn.execute(
                "UPDATE hub_social_transfers SET status='completed',completed_at=? WHERE id=?",
                (now_iso(), int(transfer_id)),
            )
            audit(conn, int(user["id"]), "trade_accept", f"交换 #{int(transfer_id)} 成交", request)
        flash(request, "交换已完成。")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(hub_url_for(request, "hub_market"), status_code=303)


@router.post("/market/trades/{transfer_id}/cancel", name="hub_trade_cancel")
def trade_cancel(request: Request, transfer_id: int, csrf_value: str = Form(..., alias="_csrf")):
    user = require_user(request)
    verify_csrf(request, csrf_value)
    try:
        with hub_transaction() as conn:
            transfer = _open_transfer(conn, transfer_id)
            if int(transfer["sender_id"]) != int(user["id"]):
                raise ValueError("只有发起方可以取消这次交换。")
            transact_asset(conn, int(user["id"]), transfer["offer_asset"], int(transfer["offer_amount"]), "交换取消，托管退回")
            conn.execute(
                "UPDATE hub_social_transfers SET status='cancelled',completed_at=? WHERE id=?",
                (now_iso(), int(transfer_id)),
            )
            audit(conn, int(user["id"]), "trade_cancel", f"交换 #{int(transfer_id)} 取消", request)
        flash(request, "交换已取消，托管资产已退回。")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(hub_url_for(request, "hub_market"), status_code=303)
