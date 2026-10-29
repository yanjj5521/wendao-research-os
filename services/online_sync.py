from __future__ import annotations

import hashlib
import json
import time
import uuid
import urllib.error
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from core.database import connect, get_setting, now_iso
from core.navigation import normalize_nav_labels, normalize_nav_layout
from services.economy import balances as local_balances
from services.game_world import ARTIFACTS, BUILDINGS
from services.progression import normalize_realm_labels
from services.sync_backend import (
    SYNC_EVENT_SCHEMA_VERSION,
    build_sync_backend,
)

AUTO_SYNC_TIMEOUT_SECONDS = 1.5
MANUAL_SYNC_TIMEOUT_SECONDS = 5.0
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 15 * 60
RETRY_AFTER_MAX_SECONDS = 24 * 60 * 60
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_PAUSE_SECONDS = 5 * 60
DEAD_LETTER_ATTEMPTS = 6
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def _provider_from_conn(conn) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='sync_provider'"
    ).fetchone()
    return str(row["value"]).strip() if row else "disabled"


def queue_event(
    conn,
    event_type: str,
    payload: dict[str, Any] | None = None,
    event_uuid: str | None = None,
    *,
    aggregate_type: str = "",
    aggregate_id: str = "",
    sequence_no: int = 0,
) -> str:
    event_uuid = event_uuid or uuid.uuid4().hex
    if _provider_from_conn(conn) == "disabled":
        return event_uuid
    conn.execute(
        """
        INSERT OR IGNORE INTO online_sync_queue(
            event_uuid,event_type,payload_json,status,created_at,schema_version,
            aggregate_type,aggregate_id,sequence_no
        ) VALUES (?,?,?,'pending',?,?,?,?,?)
        """,
        (
            event_uuid,
            event_type,
            json.dumps(payload or {}, ensure_ascii=False),
            now_iso(),
            SYNC_EVENT_SCHEMA_VERSION,
            aggregate_type[:60],
            aggregate_id[:120],
            max(0, int(sequence_no or 0)),
        ),
    )
    return event_uuid


def online_configured() -> bool:
    backend = build_sync_backend(
        get_setting("sync_provider", "disabled"),
        get_setting("hub_url", ""),
        get_setting("hub_api_token", ""),
    )
    return bool(backend.capabilities.enabled and backend.capabilities.ready)


def _cache(conn, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO online_sync_cache(key,value,updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), now_iso()),
    )


def _aware_now() -> datetime:
    return datetime.now().astimezone()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _event_delay(event_uuid: str, attempts: int) -> int:
    base = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))
    jitter_window = max(1, base // 4)
    digest = hashlib.sha256(event_uuid.encode("utf-8")).digest()
    return min(BACKOFF_MAX_SECONDS, base + int.from_bytes(digest[:2], "big") % jitter_window)


def _retry_after_seconds(error: urllib.error.HTTPError) -> int:
    raw = str(error.headers.get("Retry-After", "") if error.headers else "").strip()
    if not raw:
        return 0
    try:
        seconds = int(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.astimezone()
            seconds = round((target - _aware_now()).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, min(seconds, RETRY_AFTER_MAX_SECONDS))


def _next_attempt(event_uuid: str, attempts: int, retry_after_seconds: int = 0) -> str:
    delay = max(_event_delay(event_uuid, attempts), max(0, int(retry_after_seconds or 0)))
    return (_aware_now() + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _circuit_state() -> dict[str, Any]:
    value = cached_value("sync_circuit", {})
    return value if isinstance(value, dict) else {}


def _circuit_is_open(force: bool = False) -> tuple[bool, dict[str, Any]]:
    circuit = _circuit_state()
    if force or circuit.get("state") != "open":
        return False, circuit
    retry_at = _parse_time(str(circuit.get("retry_at", "")))
    if retry_at and retry_at <= _aware_now():
        return False, {**circuit, "state": "half_open"}
    return True, circuit


def sync_health() -> dict[str, Any]:
    provider = get_setting("sync_provider", "disabled")
    circuit = _circuit_state()
    retry_at = _parse_time(str(circuit.get("retry_at", "")))
    circuit_open = (
        circuit.get("state") == "open"
        and retry_at is not None
        and retry_at > _aware_now()
    )
    with connect() as conn:
        stats = dict(
            conn.execute(
                """
                SELECT COUNT(*) total,
                       SUM(CASE WHEN status='pending' AND dead_letter=0 THEN 1 ELSE 0 END) pending,
                       SUM(CASE WHEN status='synced' THEN 1 ELSE 0 END) synced,
                       SUM(CASE WHEN dead_letter=1 THEN 1 ELSE 0 END) paused
                FROM online_sync_queue
                """
            ).fetchone()
        )
        deferred = 0
        now = _aware_now()
        for row in conn.execute(
            "SELECT next_attempt_at FROM online_sync_queue WHERE status='pending' AND dead_letter=0"
        ):
            due = _parse_time(row["next_attempt_at"])
            deferred += int(bool(due and due > now))
    if provider == "disabled":
        label, tone = "安全离线", "offline"
    elif circuit_open:
        label, tone = "自动同步已暂停", "paused"
    elif int(stats.get("paused") or 0):
        label, tone = "有事件待人工处理", "warning"
    else:
        label, tone = "联机保护正常", "ready"
    return {
        "provider": provider,
        "label": label,
        "tone": tone,
        "circuit": circuit,
        "circuit_open": circuit_open,
        "retry_at": circuit.get("retry_at", ""),
        "queue": {
            "total": int(stats.get("total") or 0),
            "pending": int(stats.get("pending") or 0),
            "synced": int(stats.get("synced") or 0),
            "paused": int(stats.get("paused") or 0),
            "deferred": deferred,
        },
        "policy": {
            "auto_timeout_seconds": AUTO_SYNC_TIMEOUT_SECONDS,
            "failure_threshold": CIRCUIT_FAILURE_THRESHOLD,
            "circuit_pause_seconds": CIRCUIT_PAUSE_SECONDS,
            "dead_letter_attempts": DEAD_LETTER_ATTEMPTS,
            "backoff_max_seconds": BACKOFF_MAX_SECONDS,
            "respects_retry_after": True,
        },
        "last_probe": cached_value("last_connection_probe", {}),
    }


def test_connection(timeout: float = 3.0) -> dict[str, Any]:
    provider = get_setting("sync_provider", "disabled")
    backend = build_sync_backend(
        provider,
        get_setting("hub_url", ""),
        get_setting("hub_api_token", ""),
    )
    if not backend.capabilities.enabled or not backend.capabilities.ready:
        return {"ok": False, "message": backend.capabilities.detail}
    started = time.monotonic()
    try:
        ping = backend.ping(timeout=timeout)
        state = backend.bootstrap(timeout=timeout)
        latency_ms = round((time.monotonic() - started) * 1000)
        result = {
            "ok": True,
            "at": now_iso(),
            "latency_ms": latency_ms,
            "hub_version": ping.get("version") or state.get("hub_version") or "未知",
            "message": f"连接与 Token 均有效，往返约 {latency_ms} ms。",
        }
        with connect() as conn:
            _cache(conn, "last_connection_probe", result)
            _cache(conn, "sync_circuit", {"state": "closed", "failures": 0, "updated_at": now_iso()})
            conn.commit()
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "at": now_iso(),
            "message": f"连接检测失败：{exc}",
        }
        with connect() as conn:
            _cache(conn, "last_connection_probe", result)
            conn.commit()
        return result


def retry_paused_events() -> int:
    with connect() as conn:
        count = int(
            conn.execute(
                "SELECT COUNT(*) n FROM online_sync_queue WHERE dead_letter=1"
            ).fetchone()["n"]
        )
        conn.execute(
            """
            UPDATE online_sync_queue
            SET status='pending',dead_letter=0,attempts=0,last_error='',next_attempt_at=NULL
            WHERE dead_letter=1
            """
        )
        _cache(conn, "sync_circuit", {"state": "closed", "failures": 0, "updated_at": now_iso()})
        conn.commit()
    return count


def apply_state(conn, state: dict[str, Any]) -> None:
    central_balances = state.get("balances") if isinstance(state.get("balances"), dict) else {}
    current = local_balances(conn)
    for key in ("spirit_stone", "spirit_wood", "mystic_iron", "star_sand"):
        target = int(central_balances.get(key, current.get(key, 0)) or 0)
        difference = target - int(current.get(key, 0))
        if difference:
            conn.execute(
                "INSERT INTO asset_transactions(asset_key,amount,reason,created_at) VALUES (?,?,?,?)",
                (key, difference, "联机中心校准", now_iso()),
            )

    central_items = state.get("inventory") if isinstance(state.get("inventory"), list) else []
    central_keys = {str(item.get("item_key", "")) for item in central_items if item.get("item_key")}
    managed_keys = set(ARTIFACTS) | set(BUILDINGS)
    for key in managed_keys - central_keys:
        conn.execute("DELETE FROM inventory_items WHERE item_key=?", (key,))
    for item in central_items:
        key = str(item.get("item_key", ""))
        if not key:
            continue
        conn.execute(
            """INSERT INTO inventory_items(item_key,item_type,quantity,level,equipped,acquired_at,updated_at)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(item_key) DO UPDATE SET
               item_type=excluded.item_type,quantity=excluded.quantity,level=excluded.level,
               equipped=excluded.equipped,updated_at=excluded.updated_at""",
            (
                key, str(item.get("item_type", "artifact")), int(item.get("quantity", 1)),
                int(item.get("level", 1)), int(item.get("equipped", 0)), now_iso(), now_iso(),
            ),
        )

    user = state.get("user") if isinstance(state.get("user"), dict) else {}
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    if user or profile:
        conn.execute(
            """UPDATE player_profile SET display_name=?,title=?,bio=?,skills=?,capabilities=?,goals=?,avatar_symbol=?,updated_at=? WHERE id=1""",
            (
                str(user.get("display_name", "修士")), str(profile.get("title", "")),
                str(profile.get("bio", "")), str(profile.get("skills", "")),
                str(profile.get("capabilities", "")), str(profile.get("goals", "")),
                str(profile.get("avatar_symbol", "道"))[:2] or "道", now_iso(),
            ),
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('researcher_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(user.get("display_name", "修士")),),
        )

    theme = state.get("theme") if isinstance(state.get("theme"), dict) else {}
    mapping = {
        "accent": "ui_accent",
        "density": "ui_density",
        "scene": "ui_scene",
        "motion": "ui_motion",
        "geometry": "ui_geometry",
        "font_scale": "ui_font_scale",
        "home_effect": "ui_home_effect",
        "home_motto": "ui_home_motto",
        "home_poem": "ui_home_poem",
        "site_name": "site_name",
        "review_popup": "review_popup",
    }
    for source, setting_key in mapping.items():
        if source in theme:
            value = str(theme[source])
            if setting_key == "site_name" and value.strip() == "问道科研":
                value = "科研系统"
            conn.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (setting_key, value),
            )
    if isinstance(theme.get("poem_pool"), list):
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('ui_poem_pool',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                json.dumps(
                    [str(item).strip()[:120] for item in theme["poem_pool"] if str(item).strip()][:366],
                    ensure_ascii=False,
                ),
            ),
        )
    if isinstance(theme.get("realm_names"), (list, dict)):
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('realm_names',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(normalize_realm_labels(theme["realm_names"]), ensure_ascii=False),),
        )
    if isinstance(theme.get("nav_labels"), dict):
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('nav_labels',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(normalize_nav_labels(theme["nav_labels"]), ensure_ascii=False),),
        )
    if isinstance(theme.get("nav_layout"), list):
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('nav_layout',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(normalize_nav_layout(theme["nav_layout"]), ensure_ascii=False),),
        )
    # Assets are already centralised above.  Only the approved 修为 part of a
    # welfare grant needs a local activity record.  Keep grant ids locally so
    # a retry cannot mint the same 修为 twice, then acknowledge on next sync.
    try:
        applied = json.loads(get_setting("hub_welfare_applied", "[]"))
    except json.JSONDecodeError:
        applied = []
    applied_ids = {int(item) for item in applied if str(item).isdigit()}
    for grant in state.get("welfare_grants", []) if isinstance(state.get("welfare_grants"), list) else []:
        try:
            grant_id = int(grant.get("id", 0))
            xp = max(0, min(60, int(grant.get("xp", 0) or 0)))
        except (AttributeError, TypeError, ValueError):
            continue
        if grant_id <= 0 or grant_id in applied_ids:
            continue
        reason = str(grant.get("reason", "同行会福利"))[:160]
        if xp:
            conn.execute(
                "INSERT INTO activities(action,xp,detail,created_at) VALUES (?,?,?,?)",
                ("team_welfare", xp, f"同行会福利 #{grant_id}：{reason}", now_iso()),
            )
        applied_ids.add(grant_id)
        queue_event(conn, "welfare_claimed", {"grant_id": grant_id})
    retained = sorted(applied_ids)[-300:]
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('hub_welfare_applied',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(retained),),
    )
    _cache(conn, "hub_state", state)
    if state.get("latest_release") is not None:
        _cache(conn, "latest_release", state.get("latest_release"))


def sync_now(
    timeout: float = MANUAL_SYNC_TIMEOUT_SECONDS,
    *,
    force: bool = False,
    pull_if_idle: bool = True,
) -> dict[str, Any]:
    hub_url = get_setting("hub_url", "").strip().rstrip("/")
    token = get_setting("hub_api_token", "").strip()
    provider = get_setting("sync_provider", "disabled")
    backend = build_sync_backend(provider, hub_url, token)
    if not backend.capabilities.enabled:
        return {
            "ok": False,
            "message": backend.capabilities.detail,
            "synced": 0,
            "provider": provider,
        }
    if not backend.capabilities.ready:
        return {
            "ok": False,
            "message": backend.capabilities.detail,
            "synced": 0,
            "provider": provider,
        }
    circuit_open, circuit = _circuit_is_open(force=force)
    if circuit_open:
        return {
            "ok": False,
            "message": f"连续失败后已暂停自动同步，预计 {circuit.get('retry_at', '稍后')} 再试；本地功能不受影响。",
            "synced": 0,
            "provider": provider,
            "circuit_open": True,
        }
    with connect() as conn:
        candidates = [dict(row) for row in conn.execute(
            """
            SELECT * FROM online_sync_queue
            WHERE status!='synced' AND dead_letter=0
            ORDER BY id LIMIT 100
            """
        )]
    now = _aware_now()
    pending = [
        row
        for row in candidates
        if force
        or not _parse_time(row.get("next_attempt_at"))
        or _parse_time(row.get("next_attempt_at")) <= now
    ]
    if not pending and not pull_if_idle:
        return {
            "ok": True,
            "message": "当前没有到期的同步事件，本地操作已完成。",
            "synced": 0,
            "provider": provider,
            "deferred": len(candidates),
        }
    events = []
    for row in pending:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append(
            {
                "event_uuid": row["event_uuid"],
                "event_type": row["event_type"],
                "payload": payload,
                "schema_version": int(row.get("schema_version", 1) or 1),
                "aggregate_type": str(row.get("aggregate_type", "")),
                "aggregate_id": str(row.get("aggregate_id", "")),
                "sequence_no": int(row.get("sequence_no", 0) or 0),
                "occurred_at": row["created_at"],
            }
        )
    try:
        if events:
            response = backend.push(events, timeout=timeout)
            results = response.get("results", [])
            state = response.get("state", {})
        else:
            state = backend.bootstrap(timeout=timeout)
            results = []
        result_by_uuid = {
            str(item.get("event_uuid", "")): item
            for item in results
            if isinstance(item, dict)
        }
        accepted = {
            event_uuid
            for event_uuid, item in result_by_uuid.items()
            if item.get("status") in {"applied", "duplicate"}
        }
        with connect() as conn:
            for row in pending:
                if row["event_uuid"] in accepted:
                    conn.execute(
                        """
                        UPDATE online_sync_queue
                        SET status='synced',attempts=attempts+1,last_error='',synced_at=?,
                            next_attempt_at=NULL,dead_letter=0
                        WHERE id=?
                        """,
                        (now_iso(), row["id"]),
                    )
                else:
                    item = result_by_uuid.get(row["event_uuid"], {})
                    attempts = int(row.get("attempts", 0) or 0) + 1
                    rejected = item.get("status") == "rejected"
                    message = str(item.get("message") or "中心未确认该事件")[:500]
                    dead = rejected or attempts >= DEAD_LETTER_ATTEMPTS
                    conn.execute(
                        """
                        UPDATE online_sync_queue
                        SET status=?,attempts=?,last_error=?,next_attempt_at=?,dead_letter=?
                        WHERE id=?
                        """,
                        (
                            "paused" if dead else "pending",
                            attempts,
                            message,
                            None if dead else _next_attempt(row["event_uuid"], attempts),
                            1 if dead else 0,
                            row["id"],
                        ),
                    )
            apply_state(conn, state)
            _cache(conn, "last_sync", {"at": now_iso(), "results": results})
            _cache(conn, "last_sync_error", {})
            _cache(conn, "sync_circuit", {"state": "closed", "failures": 0, "updated_at": now_iso()})
            conn.commit()
        return {
            "ok": True,
            "message": "联机状态已同步。",
            "synced": len(accepted),
            "state": state,
            "results": results,
            "provider": provider,
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        message = f"中心返回 HTTP {exc.code}: {detail}"
        retryable = exc.code in TRANSIENT_HTTP_CODES
        server_retry_after = _retry_after_seconds(exc)
    except Exception as exc:
        message = f"暂时无法连接联机中心：{exc}"
        retryable = True
        server_retry_after = 0
    with connect() as conn:
        for row in pending:
            attempts = int(row.get("attempts", 0) or 0) + 1
            dead = not retryable or attempts >= DEAD_LETTER_ATTEMPTS
            conn.execute(
                """
                UPDATE online_sync_queue
                SET status=?,attempts=?,last_error=?,next_attempt_at=?,dead_letter=?
                WHERE id=?
                """,
                (
                    "paused" if dead else "pending",
                    attempts,
                    message[:500],
                    None if dead else _next_attempt(
                        row["event_uuid"],
                        attempts,
                        server_retry_after,
                    ),
                    1 if dead else 0,
                    row["id"],
                ),
            )
        prior = _circuit_state()
        failures = int(prior.get("failures", 0) or 0) + 1 if retryable else 0
        open_circuit = retryable and failures >= CIRCUIT_FAILURE_THRESHOLD
        circuit_pause = max(CIRCUIT_PAUSE_SECONDS, server_retry_after)
        circuit = {
            "state": "open" if open_circuit else "closed",
            "failures": failures,
            "retry_at": (
                (_aware_now() + timedelta(seconds=circuit_pause)).isoformat(timespec="seconds")
                if open_circuit
                else ""
            ),
            "last_error": message[:500],
            "updated_at": now_iso(),
        }
        _cache(conn, "last_sync_error", {"at": now_iso(), "message": message})
        _cache(conn, "sync_circuit", circuit)
        conn.commit()
    suffix = (
        " 已暂停自动同步5分钟，本地功能继续可用。"
        if circuit["state"] == "open"
        else " 事件已留在本地队列，稍后按退避时间重试。"
        if retryable
        else " 该错误不会自动重试，请检查地址或 Token 后手动恢复。"
    )
    return {"ok": False, "message": message + suffix, "synced": 0, "circuit": circuit}


def best_effort_sync() -> None:
    if get_setting("hub_auto_sync", "0") != "1" or not online_configured():
        return
    try:
        sync_now(
            timeout=AUTO_SYNC_TIMEOUT_SECONDS,
            force=False,
            pull_if_idle=False,
        )
    except Exception:
        pass


def cached_value(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM online_sync_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default
