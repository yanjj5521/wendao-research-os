"""Desktop embedding, installation, and persistent update contracts."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from core.version import APP_VERSION, is_newer_version
from services.team_update import MAX_UPDATE_BYTES, UPDATE_SCRIPT, _safe_package


ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_numeric_version_order_does_not_offer_downgrades(self) -> None:
        self.assertFalse(is_newer_version("1.3", "1.3.0"))
        self.assertFalse(is_newer_version("1.3.0", "1.3"))
        self.assertTrue(is_newer_version("1.10.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.2.9", "1.3.0"))
        self.assertFalse(is_newer_version(APP_VERSION, APP_VERSION))

    def test_running_instance_must_match_data_and_version(self) -> None:
        import run_local

        class FakeResponse:
            status = 200

            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.payload

        matching = FakeResponse(
            b'{"application":"wendao-research","data_root_hash":"same","app_version":"1.3.0"}'
        )
        old_version = FakeResponse(
            b'{"application":"wendao-research","data_root_hash":"same","app_version":"1.2.0"}'
        )
        with patch("run_local.urllib.request.urlopen", return_value=matching):
            self.assertTrue(run_local.is_research_os(5000, "same", "1.3.0"))
        with patch("run_local.urllib.request.urlopen", return_value=old_version):
            self.assertFalse(run_local.is_research_os(5000, "same", "1.3.0"))


class UpdateContractTests(unittest.TestCase):
    def test_update_script_preserves_data_and_can_rollback(self) -> None:
        self.assertIn("$Executable.previous", UPDATE_SCRIPT)
        self.assertIn("Stop-Process", UPDATE_SCRIPT)
        self.assertIn("Copy-Item -LiteralPath $previous -Destination $Executable", UPDATE_SCRIPT)
        self.assertIn("Get-Sha256", UPDATE_SCRIPT)
        self.assertNotIn("user_data", UPDATE_SCRIPT)
        update_source = (ROOT / "services" / "team_update.py").read_text(encoding="utf-8")
        self.assertNotIn('| getattr(subprocess, "DETACHED_PROCESS"', update_source)
        self.assertGreaterEqual(MAX_UPDATE_BYTES, 100 * 1024 * 1024)

    def test_package_validator_rejects_traversal_and_accepts_release_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-update-test-") as temp:
            root = Path(temp)
            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../WendaoResearchV3.exe", b"x")
                archive.writestr("portable.flag", b"portable")
                archive.writestr("使用说明.txt", "说明")
            with self.assertRaises(ValueError):
                _safe_package(unsafe)

            valid = root / "valid.zip"
            with zipfile.ZipFile(valid, "w") as archive:
                archive.writestr("WendaoResearchV3/WendaoResearchV3.exe", b"exe")
                archive.writestr("WendaoResearchV3/portable.flag", b"portable")
                archive.writestr("WendaoResearchV3/使用说明.txt", "说明")
            _safe_package(valid)

    def test_update_download_verifies_token_hash_and_backs_up_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-update-download-") as temp:
            script = r"""
import hashlib
import io
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core.database import init_db
from services.team_update import prepare_team_update

init_db()
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    archive.writestr("WendaoResearchV3/WendaoResearchV3.exe", b"tiny-exe")
    archive.writestr("WendaoResearchV3/portable.flag", b"portable")
    archive.writestr("WendaoResearchV3/使用说明.txt", "说明")
payload = buffer.getvalue()
digest = hashlib.sha256(payload).hexdigest()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/api/v1/releases/7/download"
        assert self.headers.get("Authorization") == "Bearer test-token"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *_args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    package = prepare_team_update(
        hub_url=f"http://127.0.0.1:{server.server_port}",
        token="test-token",
        release_id=7,
        sha256=digest,
    )
finally:
    server.shutdown()
    server.server_close()
assert package.is_file() and hashlib.sha256(package.read_bytes()).hexdigest() == digest
backups = list((Path(os.environ["RESEARCH_OS_DATA_DIR"]) / "storage" / "autobackups").glob("research_os_*.db"))
assert backups, "update download did not create a database backup"
print("update-download-ok")
"""
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = temp
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("update-download-ok", result.stdout)

    def test_runtime_version_metadata_refreshes_without_touching_preferences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-runtime-meta-") as temp:
            script = """
from core.database import get_setting, init_db, set_setting
init_db()
set_setting('portable_version', '0.0.1-old')
set_setting('ui_home_motto', '用户自己的短句')
init_db()
assert get_setting('portable_version') == __import__('core.version', fromlist=['APP_VERSION']).APP_VERSION
assert get_setting('ui_home_motto') == '用户自己的短句'
print('runtime-meta-ok')
"""
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = temp
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("runtime-meta-ok", result.stdout)

    def test_legacy_clean_v3_database_is_adopted_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wendao-legacy-adopt-") as temp:
            script = """
import sqlite3
from pathlib import Path
from core.database import DB_PATH, get_setting, init_db
from core.schema import BASE_SCHEMA_SQL, SCHEMA_VERSION

db_dir = Path(__import__('os').environ['RESEARCH_OS_DATA_DIR']) / 'instance'
db_dir.mkdir(parents=True, exist_ok=True)
db = db_dir / 'wendao-v3-clean.db'
conn = sqlite3.connect(db)
conn.executescript(BASE_SCHEMA_SQL)
conn.execute('DROP TABLE user_feedback')
conn.execute('DROP TABLE schema_migrations')
conn.execute("INSERT INTO settings(key,value) VALUES ('legacy_marker','keep-me')")
conn.execute('PRAGMA user_version = 1')
conn.commit()
conn.close()

init_db()
assert get_setting('legacy_marker') == 'keep-me'
assert DB_PATH.exists() and DB_PATH != db
conn = sqlite3.connect(DB_PATH)
assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_feedback'").fetchone()
assert conn.execute("SELECT note FROM schema_migrations WHERE version=1").fetchone()[0] == 'adopted legacy Wendao schema'
conn.close()
print('legacy-adopt-ok')
"""
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = temp
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("legacy-adopt-ok", result.stdout)

    def test_embedded_team_and_installer_contracts(self) -> None:
        app = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "online.html").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" / "install_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('app.mount("/team", hub_app', app)
        self.assertIn("同一个窗口", template)
        self.assertIn("更新只会替换程序 EXE", template)
        self.assertIn(r'$env:LOCALAPPDATA\WendaoResearchV3', installer)
        self.assertIn("CreateShortcut", installer)

    def test_cloud_deployment_uses_explicit_serve_mode(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        generator = (ROOT / "tools" / "prepare_cloud_deploy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("python run_local.py --serve", readme)
        self.assertIn("python run_local.py --serve", generator)


if __name__ == "__main__":
    unittest.main()
