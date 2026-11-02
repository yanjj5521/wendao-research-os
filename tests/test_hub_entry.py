from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from hub.routes.auth import session_reset


class SessionResetTests(unittest.TestCase):
    def test_clears_stale_session_without_csrf(self) -> None:
        request = types.SimpleNamespace(
            session={"hub_user_id": 7, "csrf": "stale-token"}
        )
        response = session_reset(request)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(request.session, {})

    def test_repeat_reset_is_idempotent(self) -> None:
        request = types.SimpleNamespace(session={})
        response = session_reset(request)
        self.assertEqual(response.status_code, 303)



class PackagedHubSelfCheckTests(unittest.TestCase):
    def test_existing_hub_without_bootstrap_password_can_self_check(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = r"""
from hub.db import HUB_ADMIN_PATH, init_hub_db
init_hub_db()
HUB_ADMIN_PATH.unlink(missing_ok=True)
from run_hub import packaged_self_check
packaged_self_check()
print("existing-hub-self-check-ok")
"""
        with tempfile.TemporaryDirectory(prefix="hub-self-check-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            env["PYTHONPATH"] = str(root)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            self.assertIn("existing-hub-self-check-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
