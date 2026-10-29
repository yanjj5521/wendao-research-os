from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.database import connect
from core.runtime_paths import STORAGE_ROOT

AUTO_BACKUP_DIR = STORAGE_ROOT / "autobackups"


def backup_local_db(*, keep: int = 14, min_interval_hours: int = 12, force: bool = False) -> Path | None:
    """Create a consistent SQLite snapshot without copying large research files."""
    AUTO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(AUTO_BACKUP_DIR.glob("research_os_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing and not force:
        newest = datetime.fromtimestamp(existing[0].stat().st_mtime)
        if datetime.now() - newest < timedelta(hours=min_interval_hours):
            return None
    target = AUTO_BACKUP_DIR / f"research_os_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    source = connect()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    for old in sorted(AUTO_BACKUP_DIR.glob("research_os_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
        old.unlink(missing_ok=True)
    return target


def register_backup_jobs(app) -> None:
    @app.on_event("startup")
    def _startup_backup() -> None:
        try:
            backup_local_db()
        except Exception:
            pass

        def _loop() -> None:
            while True:
                time.sleep(24 * 3600)
                try:
                    backup_local_db(force=True)
                except Exception:
                    pass

        threading.Thread(target=_loop, daemon=True, name="research-os-local-backup").start()
