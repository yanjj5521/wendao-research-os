"""Contracts for Hub scroll preservation across links and form redirects."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HubNavigationContractTests(unittest.TestCase):
    def test_script_is_shared_by_all_hub_templates(self) -> None:
        base = (ROOT / "hub_templates" / "base.html").read_text(encoding="utf-8")
        script = (ROOT / "hub_static" / "navigation.js").read_text(encoding="utf-8")
        self.assertIn('/static/navigation.js" defer', base)
        self.assertIn("wendao:nav-scroll", script)
        self.assertIn("MAX_AGE_MS = 15000", script)
        self.assertIn("pending.path !== null", script)
        self.assertIn("addEventListener('click'", script)
        self.assertIn("addEventListener('submit'", script)
        self.assertIn("addEventListener('pageshow'", script)
        self.assertIn("scrollHeight - window.innerHeight", script)

    def test_javascript_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, "--check", str(ROOT / "hub_static" / "navigation.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
