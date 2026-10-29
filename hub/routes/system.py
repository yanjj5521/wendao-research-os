from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from hub.common import VERSION

from hub.db import (
    init_hub_db,
    HUB_BACKUP_DIR,
    backup_hub_db,
    connect_hub,
)

router = APIRouter()


@router.on_event("startup")
def startup() -> None:
    init_hub_db()
    try:
        backup_hub_db()
    except Exception:
        pass

    def periodic_backup() -> None:
        while True:
            time.sleep(24 * 3600)
            try:
                backup_hub_db()
            except Exception:
                pass

    threading.Thread(target=periodic_backup, daemon=True).start()

@router.get("/health")
def health() -> dict[str, Any]:
    try:
        with connect_hub() as conn:
            conn.execute("SELECT 1").fetchone()
            active_members = int(
                conn.execute("SELECT COUNT(*) n FROM accounts WHERE active=1").fetchone()["n"]
            )
        backups = sorted(
            HUB_BACKUP_DIR.glob("hub_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return {
            "status": "ok",
            "version": VERSION,
            "database": "ready",
            "active_members": active_members,
            "latest_backup": backups[0].name if backups else None,
        }
    except Exception:
        return JSONResponse(
            {
                "status": "degraded",
                "version": VERSION,
                "database": "unavailable",
                "detail": "中心数据库暂不可用。",
            },
            status_code=503,
            headers={"Retry-After": "30"},
        )

@router.get("/.well-known/research-cultivation-os")
def mobile_discovery() -> dict[str, Any]:
    return {
        "product": "科研系统",
        "role": "research_hub",
        "version": VERSION,
        "mobile_client": {
            "supported": True,
            "entry_path": "/",
            "health_path": "/health",
            "private_http_allowed": True,
            "public_https_required": True,
        },
        "data_policy": {
            "research_files": "local_only",
            "hub_data": [
                "account",
                "public_profile",
                "lightweight_progress",
                "inventory",
                "resource_cards",
                "release_metadata",
            ],
        },
    }
